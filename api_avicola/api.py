from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from functools import wraps
import os
import re
import jwt
import hmac
from datetime import datetime, timedelta
import requests
import logging
import sys
import time

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_telegram_alert(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            params={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as exc:
        logger.warning(
            "event=telegram_alert_failed error_type=%s",
            type(exc).__name__,
        )


from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()


class UTCLogFormatter(logging.Formatter):
    """Formatear logs de aplicación en UTC para correlacionarlos con Docker."""
    converter = time.gmtime


def configure_component_logger(name):
    log = logging.getLogger(name)

    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            UTCLogFormatter(
                "%(asctime)sZ %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        log.addHandler(handler)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log.setLevel(getattr(logging, level_name, logging.INFO))
    log.propagate = False
    return log


logger = configure_component_logger("avicola.api")
logger.info(
    "event=logging_initialized component=api level=%s",
    logging.getLevelName(logger.level),
)


def safe_log_value(value, fallback="-", limit=100):
    """Reducir valores externos a una forma segura para logs key=value."""
    if value is None:
        return fallback

    normalized = " ".join(str(value).split()).replace("=", "_")
    return normalized[:limit] or fallback


def request_source_ip():
    """Registrar la IP observada por Flask sin alterar la configuración del proxy."""
    return safe_log_value(request.remote_addr, limit=64)


app = Flask(__name__)

environment = os.getenv("FLASK_ENV", "production").lower()

secret_key = os.getenv("SECRET_KEY")
if not secret_key or len(secret_key) < 32:
    raise RuntimeError("SECRET_KEY no definida o demasiado corta. Debe tener al menos 32 caracteres.")

ingest_api_key = os.getenv("INGEST_API_KEY")
if not ingest_api_key or len(ingest_api_key) < 32:
    raise RuntimeError("INGEST_API_KEY no definida o demasiado corta. Debe tener al menos 32 caracteres.")

security_events_api_key = os.getenv("SECURITY_EVENTS_API_KEY")
if not security_events_api_key or len(security_events_api_key) < 32:
    raise RuntimeError(
        "SECURITY_EVENTS_API_KEY no definida o demasiado corta. "
        "Debe tener al menos 32 caracteres."
    )

dashboard_proxy_key = os.getenv("DASHBOARD_PROXY_KEY")
if not dashboard_proxy_key or len(dashboard_proxy_key) < 32:
    raise RuntimeError(
        "DASHBOARD_PROXY_KEY no definida o demasiado corta. "
        "Debe tener al menos 32 caracteres."
    )

jwt_secret_key = os.getenv("JWT_SECRET_KEY")
if not jwt_secret_key or len(jwt_secret_key) < 32:
    raise RuntimeError("JWT_SECRET_KEY no definida o demasiado corta. Debe tener al menos 32 caracteres.")

jwt_access_token_expires_minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "60"))


def read_positive_security_int(name, default, minimum=1, maximum=1440):
    """Leer enteros de configuración con límites seguros."""
    raw_value = os.getenv(name, str(default))

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "event=security_config_invalid setting=%s fallback=%s",
            name,
            default,
        )
        return default

    if not minimum <= value <= maximum:
        logger.warning(
            "event=security_config_out_of_range setting=%s fallback=%s",
            name,
            default,
        )
        return default

    return value


login_max_failures = read_positive_security_int(
    "LOGIN_MAX_FAILURES",
    5,
    minimum=1,
    maximum=20,
)
login_failure_window_minutes = read_positive_security_int(
    "LOGIN_FAILURE_WINDOW_MINUTES",
    15,
)
login_lockout_minutes = read_positive_security_int(
    "LOGIN_LOCKOUT_MINUTES",
    15,
)

login_failure_window = timedelta(minutes=login_failure_window_minutes)
login_lockout_duration = timedelta(minutes=login_lockout_minutes)

app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(environment == "production"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

cors_origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:5001")
cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]

CORS(
    app,
    resources={
        r"/api/*": {"origins": cors_origins},
        r"/lecturas": {"origins": cors_origins},
    },
    supports_credentials=True
)

# Security: Rate Limiting
# We set a generous global limit but strict limits on sensitive endpoints (login/register)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["10000 per day", "2000 per hour"],
    storage_uri="memory://"
)

@app.after_request
def add_security_headers(response):
    """Add security headers to every response"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# DB PostgreSQL configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

PASSWORD_HASH_METHOD = "scrypt"
PASSWORD_HASH_SALT_LENGTH = 16
CURRENT_PASSWORD_HASH_PREFIX = "scrypt:"
SUPPORTED_PASSWORD_HASH_PREFIXES = ("scrypt:", "pbkdf2:")


def generate_secure_password_hash(password):
    """Generar hashes nuevos con un metodo fuerte y explicito."""
    return generate_password_hash(
        password,
        method=PASSWORD_HASH_METHOD,
        salt_length=PASSWORD_HASH_SALT_LENGTH,
    )


def password_hash_scheme(password_hash):
    """Identificar el esquema sin exponer el hash completo."""
    if not isinstance(password_hash, str) or not password_hash:
        return "missing"

    return password_hash.split("$", 1)[0]


def is_supported_password_hash(password_hash):
    """Aceptar solo formatos de Werkzeug, nunca texto plano."""
    if not isinstance(password_hash, str):
        return False

    if password_hash != password_hash.strip() or any(
        char.isspace()
        for char in password_hash
    ):
        return False

    if not password_hash.startswith(SUPPORTED_PASSWORD_HASH_PREFIXES):
        return False

    return password_hash.count("$") >= 2


def verify_password_hash(password_hash, password):
    """Verificar solo hashes reconocidos para evitar aceptar texto plano."""
    if not is_supported_password_hash(password_hash):
        return False

    try:
        return check_password_hash(password_hash, password)
    except (TypeError, ValueError):
        return False


class Lectura(db.Model):
    __tablename__ = 'lecturas'
    id_lectura = db.Column(db.String, primary_key=True)
    modulo = db.Column(db.String, index=True)
    hora   = db.Column(db.DateTime, index=True)
    temperatura = db.Column(db.Float)
    humedad = db.Column(db.Float)
    co = db.Column(db.Float)
    co2 = db.Column(db.Float)
    amoniaco = db.Column(db.Float)
    oxigeno = db.Column(db.Float)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)
    full_name = db.Column(db.String(120))
    role = db.Column(db.String(50))
    initials = db.Column(db.String(10))
    profile_image_url = db.Column(db.String(500))
    
    def set_password(self, password):
        """Hash the password and store it."""
        self.password_hash = generate_secure_password_hash(password)
    
    def check_password(self, password):
        """Check if the provided password matches a supported hash."""
        return verify_password_hash(self.password_hash, password)

    def has_supported_password_hash(self):
        return is_supported_password_hash(self.password_hash)

    def password_hash_needs_migration(self):
        return (
            self.has_supported_password_hash()
            and not self.password_hash.startswith(CURRENT_PASSWORD_HASH_PREFIX)
        )

    def password_hash_scheme(self):
        return password_hash_scheme(self.password_hash)


class LoginLockout(db.Model):
    """Estado persistente de fallos y bloqueos temporales por usuario."""

    __tablename__ = "login_lockouts"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    last_failed_at = db.Column(db.DateTime)
    locked_until = db.Column(db.DateTime, index=True)
    last_source_ip = db.Column(db.String(64))
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

def serialize_user(user):
    """Return safe user data for API responses."""
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "initials": user.initials,
        "profile_image_url": user.profile_image_url
    }


def generate_jwt_token(user):
    """Generate signed JWT access token for authenticated API users."""
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=jwt_access_token_expires_minutes)

    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(payload, jwt_secret_key, algorithm="HS256")


def decode_jwt_token(token):
    """Decode and validate JWT access token."""
    return jwt.decode(token, jwt_secret_key, algorithms=["HS256"])


def get_bearer_token():
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    return auth_header.split(" ", 1)[1].strip()

def validate_dashboard_proxy_key():
    """Validar la llave interna usada por el dashboard dentro de Docker."""
    provided_key = request.headers.get("X-Dashboard-Proxy-Key", "")

    if not dashboard_proxy_key or not provided_key:
        return False

    return hmac.compare_digest(provided_key, dashboard_proxy_key)


def get_dashboard_proxy_user():
    """Obtener el usuario real delegado por el dashboard autenticado."""
    if not validate_dashboard_proxy_key():
        return None

    raw_user_id = request.headers.get("X-Dashboard-User-Id", "").strip()

    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        return None

    if user_id <= 0:
        return None

    return User.query.get(user_id)


def request_audit_source_ip():
    """Obtener IP del cliente delegada por dashboard solo si la llave es válida."""
    if validate_dashboard_proxy_key():
        forwarded_ip = request.headers.get("X-Dashboard-Source-IP", "").strip()

        if forwarded_ip:
            return safe_log_value(forwarded_ip, limit=64)

    return request_source_ip()


def get_current_user_from_token():
    """Retornar usuario autenticado por JWT o por proxy interno del dashboard."""
    dashboard_user = get_dashboard_proxy_user()

    if dashboard_user:
        return dashboard_user, None

    token = get_bearer_token()

    if not token:
        return None, (jsonify({
            "error": "Token requerido.",
            "details": ["Usa el header Authorization: Bearer <token>."]
        }), 401)

    try:
        payload = decode_jwt_token(token)
        user = User.query.get(int(payload["sub"]))

        if not user:
            return None, (jsonify({
                "error": "Usuario no encontrado."
            }), 401)

        return user, None

    except jwt.ExpiredSignatureError:
        return None, (jsonify({
            "error": "Token expirado."
        }), 401)

    except jwt.InvalidTokenError:
        return None, (jsonify({
            "error": "Token inválido."
        }), 401)


def normalize_role(role):
    """Normalize role names for RBAC checks."""
    return (role or "").strip().lower()


def auth_required(roles=None):
    """Require valid JWT and optionally one of the allowed roles."""
    allowed_roles = [normalize_role(role) for role in roles] if roles else None

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            current_user, error_response = get_current_user_from_token()

            if error_response:
                return error_response

            if allowed_roles and normalize_role(current_user.role) not in allowed_roles:
                return jsonify({
                    "error": "Permisos insuficientes.",
                    "details": ["El usuario autenticado no tiene rol autorizado para esta acción."]
                }), 403

            request.current_user = current_user
            return fn(*args, **kwargs)

        return wrapper

    return decorator
        
class Umbral(db.Model):
    __tablename__ = 'umbrales'
    id = db.Column(db.Integer, primary_key=True)
    variable = db.Column(db.String(50), unique=True, nullable=False)
    valor_medio = db.Column(db.Float, nullable=False)
    valor_alto = db.Column(db.Float, nullable=False)
    valor_grave = db.Column(db.Float, nullable=False)

class Alerta(db.Model):
    __tablename__ = 'alertas'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False)  # temperature, humidity, co, co2, amoniaco
    prioridad = db.Column(db.String(20), nullable=False)  # critical, warning, info
    mensaje = db.Column(db.Text, nullable=False)
    modulo = db.Column(db.String(50), nullable=False)
    valor_actual = db.Column(db.Float)
    umbral = db.Column(db.Float)
    estado = db.Column(db.String(20), default='active')  # active, acknowledged, resolved
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    timestamp_resuelto = db.Column(db.DateTime)
    sensor = db.Column(db.String(100))

class SecurityEvent(db.Model):
    """Evento de seguridad persistente y consultable."""

    __tablename__ = "security_events"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(100), nullable=False, index=True)
    severity = db.Column(db.String(20), nullable=False, default="warning")
    source = db.Column(db.String(50), nullable=False, index=True)
    module = db.Column(db.String(50), index=True)
    source_ip = db.Column(db.String(64))
    actor_username = db.Column(db.String(80))
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


SECURITY_EVENT_SEVERITIES = {"info", "warning", "error", "critical"}
SENSITIVE_EVENT_DETAIL_MARKERS = (
    "password",
    "token",
    "secret",
    "key",
    "authorization",
    "credential",
    "certificate",
    "payload",
)


def sanitize_security_event_details(details):
    """Conservar solo metadatos seguros y legibles para auditoría."""

    sanitized = {}

    for raw_key, raw_value in details.items():
        key = safe_log_value(raw_key, limit=64).lower()

        if any(marker in key for marker in SENSITIVE_EVENT_DETAIL_MARKERS):
            continue

        sanitized[key] = safe_log_value(raw_value, limit=160)

    return sanitized


def record_security_event(
    event_type,
    severity="warning",
    source="api",
    module=None,
    source_ip=None,
    actor_username=None,
    **details,
):
    """Guardar un evento sin alterar la respuesta principal del flujo."""

    normalized_severity = safe_log_value(severity, limit=20).lower()
    if normalized_severity not in SECURITY_EVENT_SEVERITIES:
        normalized_severity = "warning"

    try:
        security_event = SecurityEvent(
            event_type=safe_log_value(event_type, limit=100),
            severity=normalized_severity,
            source=safe_log_value(source, limit=50),
            module=safe_log_value(module, limit=50) if module else None,
            source_ip=safe_log_value(source_ip, limit=64) if source_ip else None,
            actor_username=(
                safe_log_value(actor_username, limit=80)
                if actor_username
                else None
            ),
            details=sanitize_security_event_details(details),
        )

        db.session.add(security_event)
        db.session.commit()

        logger.info(
            "event=security_event_persisted security_event_type=%s "
            "severity=%s source=%s security_event_id=%s",
            security_event.event_type,
            security_event.severity,
            security_event.source,
            security_event.id,
        )

        return security_event

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=security_event_persist_failed security_event_type=%s source=%s",
            safe_log_value(event_type, limit=100),
            safe_log_value(source, limit=50),
        )
        return None


def validate_security_events_key():
    """Validar la llave técnica usada por servicios internos."""

    provided_key = request.headers.get("X-Security-Events-Key", "")

    if not security_events_api_key or not provided_key:
        return False

    return hmac.compare_digest(provided_key, security_events_api_key)


def serialize_security_event(security_event):
    """Convertir un evento persistido a una respuesta segura de API."""

    return {
        "id": security_event.id,
        "event_type": security_event.event_type,
        "severity": security_event.severity,
        "source": security_event.source,
        "module": security_event.module,
        "source_ip": security_event.source_ip,
        "actor_username": security_event.actor_username,
        "details": security_event.details or {},
        "created_at": (
            security_event.created_at.isoformat()
            if security_event.created_at
            else None
        ),
    }


def record_privileged_action(
    action,
    resource_type,
    resource_id=None,
    changed_fields=None,
    affected_count=None,
    **metadata,
):
    """Registrar una acción administrativa exitosa sin guardar payloads."""
    actor = getattr(request, "current_user", None)

    if not actor:
        logger.error(
            "event=privileged_audit_missing_actor action=%s resource_type=%s",
            safe_log_value(action, limit=80),
            safe_log_value(resource_type, limit=50),
        )
        return None

    normalized_fields = []

    if changed_fields:
        normalized_fields = sorted({
            safe_log_value(field, limit=64)
            for field in changed_fields
            if field
        })

    details = {
        "action": safe_log_value(action, limit=80),
        "resource_type": safe_log_value(resource_type, limit=50),
        "resource_id": (
            safe_log_value(resource_id, limit=80)
            if resource_id is not None
            else "none"
        ),
        "actor_user_id": actor.id,
        "actor_role": safe_log_value(actor.role, limit=32),
        "outcome": "success",
        "http_method": request.method,
        "route": safe_log_value(request.path, limit=120),
    }

    if normalized_fields:
        details["changed_fields"] = ",".join(normalized_fields)

    if affected_count is not None:
        details["affected_count"] = affected_count

    details.update(metadata)

    security_event = record_security_event(
        event_type="privileged_action",
        severity="info",
        source="api",
        source_ip=request_audit_source_ip(),
        actor_username=actor.username,
        **details,
    )

    if security_event:
        logger.info(
            "event=privileged_action_audited action=%s resource_type=%s "
            "resource_id=%s actor_user_id=%s actor_role=%s",
            details["action"],
            details["resource_type"],
            details["resource_id"],
            actor.id,
            details["actor_role"],
        )

    return security_event


def migrate_user_password_hash_if_needed(user, password, source_ip):
    """Rehashear hashes soportados pero antiguos tras un login valido."""
    if not user.password_hash_needs_migration():
        return False

    old_scheme = safe_log_value(user.password_hash_scheme(), limit=64)
    user.set_password(password)
    db.session.commit()

    logger.info(
        "event=password_hash_migrated user_id=%s old_hash_scheme=%s "
        "new_hash_scheme=%s source_ip=%s",
        user.id,
        old_scheme,
        PASSWORD_HASH_METHOD,
        source_ip,
    )
    record_security_event(
        event_type="password_hash_migrated",
        severity="info",
        source="api",
        source_ip=source_ip,
        actor_username=user.username,
        old_hash_scheme=old_scheme,
        new_hash_scheme=PASSWORD_HASH_METHOD,
    )
    return True


def normalize_login_username(username):
    """Normalizar el identificador usado para contar fallos."""
    return (username or "").strip().casefold()


def get_active_login_lockout(username, now=None):
    """Obtener un bloqueo todavía vigente para el usuario indicado."""
    now = now or datetime.utcnow()

    lockout = LoginLockout.query.filter_by(username=username).first()

    if lockout and lockout.locked_until and lockout.locked_until > now:
        return lockout

    return None


def get_login_lockout_retry_after_seconds(lockout, now=None):
    """Calcular segundos restantes para el header Retry-After."""
    now = now or datetime.utcnow()

    if not lockout or not lockout.locked_until:
        return 1

    remaining_seconds = (lockout.locked_until - now).total_seconds()
    return max(1, int(remaining_seconds + 0.999))


def register_login_failure(username, source_ip, now=None):
    """Registrar un fallo y bloquear temporalmente si alcanza el límite."""
    now = now or datetime.utcnow()

    lockout = LoginLockout.query.filter_by(username=username).first()

    if lockout is None:
        lockout = LoginLockout(
            username=username,
            failed_attempts=0,
            window_started_at=now,
            last_source_ip=source_ip,
        )
        db.session.add(lockout)

    window_expired = (
        not lockout.window_started_at
        or now - lockout.window_started_at >= login_failure_window
    )
    previous_lock_expired = (
        lockout.locked_until is not None
        and lockout.locked_until <= now
    )

    if window_expired or previous_lock_expired:
        lockout.failed_attempts = 0
        lockout.window_started_at = now
        lockout.locked_until = None

    lockout.failed_attempts += 1
    lockout.last_failed_at = now
    lockout.last_source_ip = source_ip
    lockout.updated_at = now

    lockout_triggered = lockout.failed_attempts >= login_max_failures

    if lockout_triggered:
        lockout.locked_until = now + login_lockout_duration

    db.session.commit()

    return lockout, lockout_triggered


def clear_login_lockout(username):
    """Eliminar el contador después de un inicio de sesión válido."""
    lockout = LoginLockout.query.filter_by(username=username).first()

    if not lockout:
        return

    db.session.delete(lockout)
    db.session.commit()

    logger.info(
        "event=login_lockout_cleared username=%s",
        safe_log_value(username, limit=64),
    )


def build_login_lockout_response(retry_after_seconds):
    """Responder sin revelar detalles sensibles sobre la cuenta."""
    response = jsonify({
        "error": "Cuenta bloqueada temporalmente. Intenta de nuevo más tarde.",
        "retry_after_seconds": retry_after_seconds,
    })
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after_seconds)
    response.headers["Cache-Control"] = "no-store"
    return response


class Granja(db.Model):
    __tablename__ = 'granjas'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    ubicacion = db.Column(db.String(200))
    naves = db.relationship('Nave', backref='granja', lazy=True)

class Nave(db.Model):
    __tablename__ = 'naves'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    fecha_inicio_parvada = db.Column(db.Date, nullable=True)
    granja_id = db.Column(db.Integer, db.ForeignKey('granjas.id'), nullable=False)
    modulos = db.relationship('Modulo', backref='nave', lazy=True)

class Modulo(db.Model):
    __tablename__ = 'modulos'
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), unique=True, nullable=False)
    nombre = db.Column(db.String(100))
    nave_id = db.Column(db.Integer, db.ForeignKey('naves.id'), nullable=True)

# Create tables and indexes if they don't exist
with app.app_context():
    db.create_all()
    with db.engine.connect() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_lecturas_modulo ON lecturas(modulo)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_lecturas_hora ON lecturas(hora)"
        ))
        conn.commit()

from datetime import date as date_type

def calcular_semana_parvada(fecha_inicio):
    if fecha_inicio is None:
        return None
    today = date_type.today()
    days = (today - fecha_inicio).days
    if days < 0:
        return None
    return {'semana': (days // 7) + 1, 'dia_semana': (days % 7) + 1, 'dias_totales': days}

# Function to check and create alerts
def check_and_create_alerts():
    """Check latest readings and create alerts if thresholds are exceeded"""
    umbrales = {u.variable: u for u in Umbral.query.all()}
    
    # Get latest reading for each module
    latest_readings = {}
    for lectura in Lectura.query.order_by(Lectura.hora.desc()).limit(100).all():
        if lectura.modulo not in latest_readings:
            latest_readings[lectura.modulo] = lectura
    
    for modulo, lectura in latest_readings.items():
        # Nombres legibles para mensajes
        nombres_variable = {
            'temperatura': 'Temperatura',
            'humedad': 'Humedad',
            'co': 'CO',
            'co2': 'CO₂',
            'amoniaco': 'Amoniaco',
            'oxigeno': 'Oxígeno',
        }

        # Check each parameter
        checks = [
            ('temperatura', lectura.temperatura, '°C'),
            ('humedad', lectura.humedad, '%'),
            ('co', lectura.co, 'ppm'),
            ('co2', lectura.co2, 'ppm'),
            ('amoniaco', lectura.amoniaco, 'ppm'),
            ('oxigeno', lectura.oxigeno, '% vol.'),
        ]
        
        for variable, valor, unidad in checks:
            if valor is None:
                continue
                
            umbral = umbrales.get(variable)
            if not umbral:
                continue
            
            # Check for recent alerts (Throttling / Debounce)
            # Avoid creating a new alert if one was created recently for the same module/variable
            last_alert = Alerta.query.filter_by(
                tipo=variable,
                modulo=modulo
            ).order_by(Alerta.timestamp.desc()).first()
            
            if last_alert:
                # Calculate time difference
                time_diff = datetime.utcnow() - last_alert.timestamp
                # If less than 60 seconds has passed, skip creating a new alert
                if time_diff.total_seconds() < 60:
                    logger.debug(
                    "event=alert_skipped_recent variable=%s module=%s elapsed_seconds=%s",
                    safe_log_value(variable, limit=64),
                    safe_log_value(modulo, limit=32),
                    int(time_diff.total_seconds()),
                    )
                    continue

            # Determine priority and create alert
            nombre_legible = nombres_variable.get(variable, variable.title())

            if valor >= umbral.valor_grave:
                prioridad = 'critical'
                mensaje = (
                    f"{nombre_legible} en {modulo} superó el umbral CRÍTICO: "
                    f"{valor:.2f} {unidad} (umbral {umbral.valor_grave:.2f} {unidad})"
                )
            elif valor >= umbral.valor_alto:
                prioridad = 'warning'
                mensaje = (
                    f"{nombre_legible} en {modulo} superó el umbral ALTO: "
                    f"{valor:.2f} {unidad} (umbral {umbral.valor_alto:.2f} {unidad})"
                )
            else:
                continue
            
            nueva_alerta = Alerta(
                tipo=variable,
                prioridad=prioridad,
                mensaje=mensaje,
                modulo=modulo,
                valor_actual=valor,
                umbral=umbral.valor_alto if prioridad == 'warning' else umbral.valor_grave,
                sensor=f"{variable.title()} Sensor #{modulo}"
            )
            db.session.add(nueva_alerta)

            if prioridad == 'critical':
                ts = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                send_telegram_alert(
                    f"🚨 <b>ALERTA CRÍTICA — {modulo}</b>\n"
                    f"<b>{nombre_legible}:</b> {valor:.2f} {unidad}\n"
                    f"<b>Umbral crítico:</b> {umbral.valor_grave:.2f} {unidad}\n"
                    f"🕐 {ts}"
                )

    db.session.commit()

# MQTT endpoint
SENSOR_LIMITS = {
    "temperatura": (-40.0, 125.0),
    "humedad": (0.0, 100.0),
    "co": (0.0, 500.0),
    "co2": (400.0, 5000.0),
    "amoniaco": (0.0, 100.0),
    "oxigeno": (0.0, 25.0),
}

REQUIRED_LECTURA_FIELDS = [
    "id_lectura",
    "modulo",
    "hora",
    "temperatura",
    "humedad",
    "co",
    "co2",
    "amoniaco",
    "oxigeno",
]


def _parse_sensor_float(value, field_name, errors):
    """Convertir valores de sensores a float y rechazar tipos inválidos."""
    if isinstance(value, bool):
        errors.append(f"'{field_name}' debe ser numérico, no booleano.")
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"'{field_name}' debe ser un valor numérico.")
        return None


def _parse_iso_datetime(value, errors):
    """Validar fecha/hora en formato ISO 8601."""
    if not isinstance(value, str) or not value.strip():
        errors.append("'hora' debe ser una fecha/hora en formato ISO 8601.")
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append("'hora' debe tener formato ISO 8601 válido.")
        return None


def validate_lectura_payload(data):
    """Validar y normalizar payload de lectura de sensores."""
    errors = []

    if not isinstance(data, dict):
        return None, ["El cuerpo de la solicitud debe ser un objeto JSON."]

    missing_fields = [field for field in REQUIRED_LECTURA_FIELDS if field not in data]
    if missing_fields:
        errors.append(f"Campos requeridos faltantes: {', '.join(missing_fields)}.")

    id_lectura = data.get("id_lectura")
    if not isinstance(id_lectura, str) or not id_lectura.strip():
        errors.append("'id_lectura' debe ser texto no vacío.")
    elif len(id_lectura.strip()) > 100:
        errors.append("'id_lectura' no debe exceder 100 caracteres.")

    modulo = data.get("modulo")
    if not isinstance(modulo, str) or not modulo.strip():
        errors.append("'modulo' debe ser texto no vacío.")
    elif not re.match(r"^[A-Za-z0-9_-]{1,50}$", modulo.strip()):
        errors.append("'modulo' solo puede contener letras, números, guion y guion bajo; máximo 50 caracteres.")

    hora = _parse_iso_datetime(data.get("hora"), errors)

    normalized = {
        "id_lectura": id_lectura.strip() if isinstance(id_lectura, str) else id_lectura,
        "modulo": modulo.strip() if isinstance(modulo, str) else modulo,
        "hora": hora,
    }

    for field_name, (min_value, max_value) in SENSOR_LIMITS.items():
        numeric_value = _parse_sensor_float(data.get(field_name), field_name, errors)

        if numeric_value is not None:
            if numeric_value < min_value or numeric_value > max_value:
                errors.append(
                    f"'{field_name}' fuera de rango permitido ({min_value} a {max_value})."
                )

        normalized[field_name] = numeric_value

    if errors:
        return None, errors

    return normalized, []

def validate_ingest_key():
    """Validate X-Ingest-Key header for sensor ingestion."""
    expected_key = os.getenv("INGEST_API_KEY")
    provided_key = request.headers.get("X-Ingest-Key", "")

    if not expected_key or not provided_key:
        return False

    return hmac.compare_digest(provided_key, expected_key)

@app.route('/lecturas', methods=['POST'])
@limiter.exempt
def insert_lectura():
    """Endpoint MQTT insertions with input validation."""
    try:
        if not validate_ingest_key():
            logger.warning(
                "event=telemetry_ingest_unauthorized source_ip=%s",
                request_source_ip(),
            )
            record_security_event(
                event_type="telemetry_ingest_unauthorized",
                severity="warning",
                source="api",
                source_ip=request_source_ip(),
                reason="invalid_or_missing_ingest_key",
            )
            return jsonify({
                "error": "No autorizado.",
                "details": ["Se requiere una llave de ingestión válida."]
            }), 401
        data = request.get_json(silent=True)

        if data is None:
            logger.warning(
                "event=telemetry_rejected reason=json_missing source_ip=%s",
                request_source_ip(),
            )
            record_security_event(
                event_type="telemetry_rejected",
                severity="warning",
                source="api",
                source_ip=request_source_ip(),
                reason="json_missing",
            )
            return jsonify({
                "error": "JSON inválido o ausente.",
                "details": ["La solicitud debe incluir un cuerpo JSON válido."]
            }), 400

        validated_data, validation_errors = validate_lectura_payload(data)

        if validation_errors:
            raw_module = data.get("modulo") if isinstance(data, dict) else None
            logger.warning(
                "event=telemetry_rejected reason=validation_failed module=%s "
                "error_count=%s source_ip=%s",
                safe_log_value(raw_module, limit=32),
                len(validation_errors),
                request_source_ip(),
            )
            record_security_event(
                event_type="telemetry_rejected",
                severity="warning",
                source="api",
                module=raw_module,
                source_ip=request_source_ip(),
                reason="validation_failed",
                error_count=len(validation_errors),
            )
            return jsonify({
                "error": "Validación fallida.",
                "details": validation_errors
            }), 422

        logger.info(
            "event=telemetry_validated module=%s reading_id=%s source_ip=%s",
            safe_log_value(validated_data["modulo"], limit=32),
            safe_log_value(validated_data["id_lectura"]),
            request_source_ip(),
        )

        nueva_lectura = Lectura(
            id_lectura=validated_data['id_lectura'],
            modulo=validated_data['modulo'],
            hora=validated_data['hora'],
            temperatura=validated_data['temperatura'],
            humedad=validated_data['humedad'],
            co=validated_data['co'],
            co2=validated_data['co2'],
            amoniaco=validated_data['amoniaco'],
            oxigeno=validated_data["oxigeno"],
        )

        db.session.add(nueva_lectura)
        db.session.commit()

        try:
            check_and_create_alerts()
        except Exception:
            logger.exception(
                "event=alert_creation_failed module=%s reading_id=%s",
                safe_log_value(validated_data["modulo"], limit=32),
                safe_log_value(validated_data["id_lectura"]),
            )

        logger.info(
            "event=telemetry_inserted module=%s reading_id=%s",
            safe_log_value(validated_data["modulo"], limit=32),
            safe_log_value(validated_data["id_lectura"]),
        )

        return jsonify({
            "msg": "Record inserted successfully MQTT - DB",
            "id_lectura": validated_data["id_lectura"]
        }), 201

    except IntegrityError:
        db.session.rollback()
        logger.warning(
            "event=telemetry_insert_rejected reason=duplicate source_ip=%s",
            request_source_ip(),
        )
        return jsonify({
            "error": "Registro duplicado.",
            "details": ["Ya existe una lectura con el mismo id_lectura."]
        }), 409

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=telemetry_insert_failed source_ip=%s",
            request_source_ip(),
        )
        return jsonify({
            "error": "Error interno al insertar lectura."
        }), 500


# ENDPINT to get the last record  
@app.route('/lecturas', methods=['GET'])
def get_lecturas():
    """Endpoint para obtener lecturas (formato array)"""
    try:
        # Get module parameter, default to M1
        modulo = request.args.get('modulo', 'M1')
        lectura = db.session.query(Lectura).where(Lectura.modulo == modulo).order_by(Lectura.hora.desc()).first()
        if not lectura:
            return jsonify([])

        lista = [{
            'id_lectura': lectura.id_lectura,
            'modulo': lectura.modulo,
            'timestamp': lectura.hora.isoformat() if hasattr(lectura.hora, 'isoformat') else str(lectura.hora),
            'temperatura': lectura.temperatura,
            'humedad': lectura.humedad,
            'co': lectura.co,
            'co2': lectura.co2,
            'amoniaco': lectura.amoniaco,
            'oxigeno': lectura.oxigeno,
            'tvoc': 0,  # TVOC no está en la BD, valor por defecto
            'sync_time': datetime.now().isoformat()
        }]
        return jsonify(lista)
    except Exception as e:
        logger.exception(
        "event=latest_record_query_failed module=%s source_ip=%s",
        safe_log_value(locals().get("modulo"), limit=32),
        request_source_ip(),
        )
        return jsonify({'error': str(e)}), 500

# Live data endpoint for dashboard
@app.route('/api/live-data', methods=['GET'])
@limiter.exempt  # Exempt from rate limiting because it's polled frequently
def get_live_data():
    """Endpoint para obtener datos en tiempo real (formato objeto)"""
    try:
        modulo = request.args.get('modulo')
        if not modulo:
            return jsonify({}), 200
        lectura = db.session.query(Lectura).where(Lectura.modulo == modulo).order_by(Lectura.hora.desc()).first()
        if not lectura:
            return jsonify({})

        data = {
            'id_lectura': lectura.id_lectura,
            'modulo': lectura.modulo,
            'timestamp': lectura.hora.isoformat() if hasattr(lectura.hora, 'isoformat') else str(lectura.hora),
            'temperatura': lectura.temperatura,
            'humedad': lectura.humedad,
            'co': lectura.co,
            'co2': lectura.co2,
            'amoniaco': lectura.amoniaco,
            'oxigeno': lectura.oxigeno,
            'tvoc': 0,  # TVOC no está en la BD, valor por defecto
            'sync_time': datetime.now().isoformat()
        }
        return jsonify(data)
    except Exception as e:
        logger.exception(
        "event=live_data_query_failed module=%s source_ip=%s",
        safe_log_value(locals().get("modulo"), limit=32),
        request_source_ip(),
        )
        return jsonify({'error': str(e)}), 500



@app.route('/api/historical')
@limiter.exempt
def historical_data():
    try:
        from datetime import datetime, timedelta
        
        range_param = request.args.get('range', '24h')
        from_date = request.args.get('from')
        to_date = request.args.get('to')
        house_param = request.args.get('modulo')
        
        # Calcular el tiempo límite según el rango
        now = datetime.now()
        if range_param == '1m':
            limit_time = now - timedelta(minutes=1)
        elif range_param == '10m':
            limit_time = now - timedelta(minutes=10)
        elif range_param == '30m':
            limit_time = now - timedelta(minutes=30)
        elif range_param == '1h':
            limit_time = now - timedelta(hours=1)
        elif range_param == '2h':
            limit_time = now - timedelta(hours=2)
        elif range_param == '12h':
            limit_time = now - timedelta(hours=12)
        elif range_param == '24h':
            limit_time = now - timedelta(hours=24)
        elif range_param == '3d':
            limit_time = now - timedelta(days=3)
        elif range_param == '7d':
            limit_time = now - timedelta(days=7)
        elif range_param == '30d':
            limit_time = now - timedelta(days=30)
        elif range_param == '90d':
            limit_time = now - timedelta(days=90)
        elif range_param == 'custom' and from_date and to_date:
            # Parse datetime-local values (ISO format without timezone)
            limit_time = datetime.fromisoformat(from_date)
            to_time = datetime.fromisoformat(to_date)
            query = Lectura.query.filter(
                Lectura.hora >= limit_time,
                Lectura.hora <= to_time
            )
        else:
            # Por defecto últimas 24 horas
            limit_time = now - timedelta(hours=24)
            query = Lectura.query.filter(Lectura.hora >= limit_time)

        # Si no es custom, inicializar query con el filtro de tiempo
        if range_param != 'custom':
            query = Lectura.query.filter(Lectura.hora >= limit_time)
            
        # Aplicar filtro de casa si existe
        if house_param and house_param != 'all':
            query = query.filter(Lectura.modulo == house_param)
            
        lecturas = query.order_by(Lectura.hora).all()

        # Downsample para visualización: máximo 1500 puntos por gráfica
        MAX_POINTS = 1500
        if len(lecturas) > MAX_POINTS:
            step = len(lecturas) // MAX_POINTS
            lecturas = lecturas[::step]

        logger.debug(
        "event=historical_query_completed range=%s module=%s record_count=%s",
        safe_log_value(range_param, limit=32),
        safe_log_value(house_param, limit=32),
        len(lecturas),
        )
        
        if not lecturas:
            logger.debug(
            "event=historical_query_empty range=%s module=%s",
            safe_log_value(range_param, limit=32),
            safe_log_value(house_param, limit=32),
            )
            return jsonify({
                "timestamps": [],
                "house": [],
                "temperature": [],
                "humidity": [],
                "ammonia": [],
                "co": [],
                "co2": [],
                "oxygen": [],
            })
        
        data = {
            "timestamps": [l.hora.isoformat() for l in lecturas],
            "house": [l.modulo for l in lecturas],
            "temperature": [l.temperatura for l in lecturas],
            "humidity": [l.humedad for l in lecturas],
            "ammonia": [l.amoniaco for l in lecturas],
            "co": [l.co for l in lecturas],
            "co2": [l.co2 for l in lecturas],
            "oxygen": [l.oxigeno for l in lecturas],
        }
        return jsonify(data)
    except Exception as e:
        logger.exception(
        "event=historical_query_failed range=%s module=%s source_ip=%s",
        safe_log_value(locals().get("range_param"), limit=32),
        safe_log_value(locals().get("house_param"), limit=32),
        request_source_ip(),
        )
        return jsonify({
            "timestamps": [],
            "temperature": [],
            "humidity": [],
            "ammonia": [],
            "co": [],
            "co2": [],
            "error": str(e)
        })

# User Management Endpoints
@app.route('/api/register', methods=['POST'])
@limiter.limit("5 per hour")  # Restrict registration to prevent spam
def register_user():
    try:
        data = request.get_json()
        if User.query.filter_by(username=data['username']).first():
            return jsonify({'error': 'Username already exists'}), 400
        
        user = User(
            username=data['username'],
            full_name=data.get('full_name', ''),
            role=data.get('role', 'User'),
            initials=data.get('initials', ''),
            profile_image_url=data.get('profile_image_url', '')
        )
        user.set_password(data['password'])
        db.session.add(user)
        db.session.commit()
        return jsonify({'msg': 'User registered successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
@limiter.limit("10 per minute")  # Protección complementaria por IP
def login_user():
    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "error": "JSON inválido o ausente."
            }), 400

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({
                "error": "Usuario y contraseña son requeridos."
            }), 400

        if len(username) > 80:
            return jsonify({
                "error": "Usuario inválido."
            }), 400

        lockout_username = normalize_login_username(username)
        source_ip = request_source_ip()
        now = datetime.utcnow()

        active_lockout = get_active_login_lockout(lockout_username, now)

        if active_lockout:
            retry_after_seconds = get_login_lockout_retry_after_seconds(
                active_lockout,
                now,
            )

            logger.warning(
                "event=login_blocked username=%s source_ip=%s retry_after_seconds=%s",
                safe_log_value(username, limit=64),
                source_ip,
                retry_after_seconds,
            )
            record_security_event(
                event_type="login_blocked",
                severity="warning",
                source="api",
                source_ip=source_ip,
                actor_username=username,
                reason="lockout_active",
                retry_after_seconds=retry_after_seconds,
            )
            return build_login_lockout_response(retry_after_seconds)

        user = User.query.filter_by(username=username).first()

        if user and not user.has_supported_password_hash():
            logger.error(
                "event=password_hash_unsupported user_id=%s "
                "hash_scheme=%s source_ip=%s",
                user.id,
                safe_log_value(user.password_hash_scheme(), limit=64),
                source_ip,
            )
            record_security_event(
                event_type="password_hash_unsupported",
                severity="error",
                source="api",
                source_ip=source_ip,
                actor_username=username,
                reason="unsupported_hash_scheme",
                hash_scheme=user.password_hash_scheme(),
            )

        if user and user.check_password(password):
            migrate_user_password_hash_if_needed(user, password, source_ip)
            clear_login_lockout(lockout_username)

            access_token = generate_jwt_token(user)
            logger.info(
                "event=login_succeeded user_id=%s username=%s role=%s source_ip=%s",
                user.id,
                safe_log_value(user.username, limit=64),
                safe_log_value(user.role, limit=32),
                source_ip,
            )

            return jsonify({
                "msg": "Login successful",
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": jwt_access_token_expires_minutes * 60,
                "user": serialize_user(user)
            }), 200

        lockout, lockout_triggered = register_login_failure(
            lockout_username,
            source_ip,
            now,
        )

        logger.warning(
            "event=login_failed username=%s source_ip=%s failed_attempts=%s",
            safe_log_value(username, limit=64),
            source_ip,
            lockout.failed_attempts,
        )
        record_security_event(
            event_type="login_failed",
            severity="warning",
            source="api",
            source_ip=source_ip,
            actor_username=username,
            reason="invalid_credentials",
            failed_attempts=lockout.failed_attempts,
        )

        if lockout_triggered:
            retry_after_seconds = get_login_lockout_retry_after_seconds(
                lockout,
                now,
            )

            logger.warning(
                "event=login_lockout_triggered username=%s source_ip=%s "
                "failed_attempts=%s retry_after_seconds=%s",
                safe_log_value(username, limit=64),
                source_ip,
                lockout.failed_attempts,
                retry_after_seconds,
            )
            record_security_event(
                event_type="login_lockout_triggered",
                severity="warning",
                source="api",
                source_ip=source_ip,
                actor_username=username,
                reason="max_failed_attempts_reached",
                failed_attempts=lockout.failed_attempts,
                retry_after_seconds=retry_after_seconds,
            )
            return build_login_lockout_response(retry_after_seconds)

        return jsonify({"error": "Invalid credentials"}), 401

    except Exception:
        logger.exception(
            "event=login_processing_failed source_ip=%s",
            request_source_ip(),
        )
        return jsonify({"error": "Error interno durante login."}), 500


@app.route('/api/auth/verify', methods=['GET'])
def verify_auth_token():
    token = get_bearer_token()

    if not token:
        return jsonify({
            "error": "Token requerido.",
            "details": ["Usa el header Authorization: Bearer <token>."]
        }), 401

    try:
        payload = decode_jwt_token(token)
        user = User.query.get(int(payload["sub"]))

        if not user:
            return jsonify({"error": "Usuario no encontrado."}), 401

        return jsonify({
            "valid": True,
            "user": serialize_user(user)
        }), 200

    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token expirado."}), 401

    except jwt.InvalidTokenError:
        return jsonify({"error": "Token inválido."}), 401

@app.route('/api/user/<int:user_id>', methods=['GET', 'PUT'])
@auth_required()
def user_detail(user_id):
    try:
        user = User.query.get(user_id)

        if not user:
            return jsonify({'error': 'User not found'}), 404

        if request.method == 'GET':
            return jsonify({
                'id': user.id,
                'username': user.username,
                'full_name': user.full_name,
                'role': user.role,
                'initials': user.initials,
                'profile_image_url': user.profile_image_url
            })

        actor = request.current_user
        actor_role = normalize_role(actor.role)
        is_self_update = actor.id == user.id

        if not is_self_update and actor_role != 'admin':
            return jsonify({
                'error': 'Permisos insuficientes.',
                'details': [
                    'Solo un administrador puede modificar otros usuarios.'
                ]
            }), 403

        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict):
            return jsonify({
                'error': 'Datos de usuario inválidos'
            }), 400

        allowed_fields = {
            'full_name',
            'initials',
            'profile_image_url',
            'role',
        }

        invalid_fields = set(data) - allowed_fields
        if invalid_fields:
            return jsonify({
                'error': 'Solicitud contiene campos no permitidos.'
            }), 400

        changed_fields = []

        for field, max_length in (
            ('full_name', 120),
            ('initials', 10),
            ('profile_image_url', 500),
        ):
            if field not in data:
                continue

            value = data[field]

            if not isinstance(value, str):
                return jsonify({
                    'error': f'El campo {field} debe ser texto.'
                }), 400

            value = value.strip()

            if len(value) > max_length:
                return jsonify({
                    'error': f'El campo {field} excede la longitud permitida.'
                }), 400

            if getattr(user, field) != value:
                setattr(user, field, value)
                changed_fields.append(field)

        if 'role' in data:
            if actor_role != 'admin':
                return jsonify({
                    'error': 'Permisos insuficientes.',
                    'details': [
                        'Solo un administrador puede cambiar roles.'
                    ]
                }), 403

            if is_self_update:
                return jsonify({
                    'error': 'No puedes cambiar tu propio rol desde el perfil.'
                }), 403

            requested_role = str(data['role']).strip().lower()

            valid_roles = {
                'admin': 'admin',
                'user': 'User',
            }

            if requested_role not in valid_roles:
                return jsonify({
                    'error': 'Rol inválido.'
                }), 400

            new_role = valid_roles[requested_role]

            if user.role != new_role:
                user.role = new_role
                changed_fields.append('role')

        if not changed_fields:
            return jsonify({
                'msg': 'No changes detected'
            }), 200

        db.session.commit()

        record_privileged_action(
            action='user_updated',
            resource_type='user',
            resource_id=user.id,
            changed_fields=changed_fields,
            target_username=safe_log_value(
                user.username,
                limit=80,
            ),
        )

        return jsonify({
            'msg': 'User updated successfully'
        })

    except Exception:
        db.session.rollback()
        logger.exception(
            'event=user_update_failed user_id=%s source_ip=%s',
            user_id,
            request_audit_source_ip(),
        )
        return jsonify({
            'error': 'Error interno al actualizar usuario.'
        }), 500

# Thresholds Endpoints
@app.route('/api/umbrales', methods=['GET'])
def get_umbrales():
    try:
        umbrales = Umbral.query.all()
        result = []
        for u in umbrales:
            result.append({
                'variable': u.variable,
                'valor_medio': u.valor_medio,
                'valor_alto': u.valor_alto,
                'valor_grave': u.valor_grave
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/umbrales', methods=['POST'])
@auth_required(roles=["admin", "operador"])
def update_umbrales():
    try:
        data = request.get_json(silent=True)

        if not isinstance(data, list) or not data:
            return jsonify({
                'error': 'Se requiere una lista de umbrales.'
            }), 400

        updated_variables = []

        for item in data:
            if not isinstance(item, dict):
                return jsonify({
                    'error': 'Cada umbral debe ser un objeto válido.'
                }), 400

            try:
                variable = str(item['variable']).strip()
                valor_medio = float(item['valor_medio'])
                valor_alto = float(item['valor_alto'])
                valor_grave = float(item['valor_grave'])
            except (KeyError, TypeError, ValueError):
                return jsonify({
                    'error': 'Datos de umbral inválidos.'
                }), 400

            if not variable:
                return jsonify({
                    'error': 'La variable del umbral es requerida.'
                }), 400

            if not (valor_medio < valor_alto < valor_grave):
                return jsonify({
                    'error': (
                        f"Umbrales inválidos para '{variable}': "
                        f"debe cumplirse medio ({valor_medio}) < "
                        f"alto ({valor_alto}) < grave ({valor_grave})"
                    )
                }), 400

            umbral = Umbral.query.filter_by(variable=variable).first()

            if umbral:
                umbral.valor_medio = valor_medio
                umbral.valor_alto = valor_alto
                umbral.valor_grave = valor_grave
            else:
                umbral = Umbral(
                    variable=variable,
                    valor_medio=valor_medio,
                    valor_alto=valor_alto,
                    valor_grave=valor_grave,
                )
                db.session.add(umbral)

            updated_variables.append(variable)

        db.session.commit()

        logger.info(
            "event=thresholds_updated actor_user_id=%s actor_role=%s "
            "updated_count=%s source_ip=%s",
            request.current_user.id,
            safe_log_value(request.current_user.role, limit=32),
            len(updated_variables),
            request_audit_source_ip(),
        )

        record_privileged_action(
            action='thresholds_updated',
            resource_type='threshold_set',
            resource_id='global',
            changed_fields=[
                'valor_medio',
                'valor_alto',
                'valor_grave',
            ],
            affected_count=len(updated_variables),
            variables=','.join(sorted({
                safe_log_value(variable, limit=50)
                for variable in updated_variables
            })),
        )

        return jsonify({
            'message': 'Thresholds updated successfully'
        }), 200

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=thresholds_update_failed actor_user_id=%s source_ip=%s",
            getattr(getattr(request, "current_user", None), "id", "-"),
            request_audit_source_ip(),
        )
        return jsonify({
            'error': 'Error interno al actualizar umbrales.'
        }), 500

@app.route('/api/umbrales/init', methods=['POST'])
@auth_required(roles=["admin"])
def init_umbrales():
    """Inicializar umbrales predeterminados solo cuando no existen."""
    try:
        existing_count = Umbral.query.count()

        if existing_count > 0:
            return jsonify({
                'message': f'Thresholds already exist ({existing_count} records)',
                'action': 'none'
            }), 200

        default_umbrales = [
            {'variable': 'temperatura', 'valor_medio': 25.0, 'valor_alto': 30.0, 'valor_grave': 35.0},
            {'variable': 'humedad', 'valor_medio': 60.0, 'valor_alto': 75.0, 'valor_grave': 85.0},
            {'variable': 'amoniaco', 'valor_medio': 20.0, 'valor_alto': 30.0, 'valor_grave': 40.0},
            {'variable': 'co2', 'valor_medio': 1000.0, 'valor_alto': 1500.0, 'valor_grave': 2000.0},
            {'variable': 'co', 'valor_medio': 10.0, 'valor_alto': 20.0, 'valor_grave': 30.0},
        ]

        for item in default_umbrales:
            db.session.add(Umbral(
                variable=item['variable'],
                valor_medio=item['valor_medio'],
                valor_alto=item['valor_alto'],
                valor_grave=item['valor_grave'],
            ))

        db.session.commit()

        record_privileged_action(
            action='thresholds_initialized',
            resource_type='threshold_set',
            resource_id='global',
            changed_fields=[
                'valor_medio',
                'valor_alto',
                'valor_grave',
            ],
            affected_count=len(default_umbrales),
            initialization='default_values',
            variables=','.join(
                item['variable']
                for item in default_umbrales
            ),
        )

        return jsonify({
            'message': f'Default thresholds created ({len(default_umbrales)} records)',
            'action': 'created',
            'count': len(default_umbrales)
        }), 201

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=thresholds_initialization_failed actor_user_id=%s source_ip=%s",
            getattr(getattr(request, "current_user", None), "id", "-"),
            request_audit_source_ip(),
        )
        return jsonify({
            'error': 'Error interno al inicializar umbrales.'
        }), 500



def get_historical_data(range, from_date=None, to_date=None):
    """Obtiene datos históricos desde la API"""
    try:
        url = f"http://localhost:5000/api/historical?range={range}"
        if from_date and to_date:
            url += f"&from={from_date}&to={to_date}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data
        else:
            logger.warning(
            "event=historical_proxy_http_error http_status=%s range=%s",
            response.status_code,
            safe_log_value(range, limit=32),
            )
            return None
    except Exception as e:
        logger.exception(
        "event=historical_proxy_request_failed range=%s",
        safe_log_value(range, limit=32),
        )
        return None

# Alerts API endpoints
@app.route('/api/alerts', methods=['GET'])
@limiter.exempt
def get_alerts():
    """Get all alerts with filtering options"""
    try:
        priority = request.args.get('priority', 'all')
        status = request.args.get('status', 'all')
        modulo = request.args.get('modulo', 'all')
        limit = request.args.get('limit', 100, type=int)
        
        query = Alerta.query
        
        if priority != 'all':
            query = query.filter_by(prioridad=priority)
        if status != 'all':
            query = query.filter_by(estado=status)
        if modulo != 'all':
            query = query.filter_by(modulo=modulo)
        
        alerts = query.order_by(Alerta.timestamp.desc()).limit(limit).all()

        # Devolver claves tanto en inglés como en español para compatibilidad
        result = []
        for alert in alerts:
            item = {
                # Identificador
                'id': alert.id,

                # Campos en inglés (usados por algunos clientes)
                'priority': alert.prioridad,
                'type': alert.tipo,
                'message': alert.mensaje,
                'house': alert.modulo,
                'timestamp': alert.timestamp.isoformat(),
                'status': alert.estado,
                'value': alert.valor_actual,
                'threshold': alert.umbral,
                'sensor': alert.sensor,
                'resolved_at': alert.timestamp_resuelto.isoformat() if alert.timestamp_resuelto else None,

                # Alias en español para el dashboard actual
                'prioridad': alert.prioridad,
                'tipo': alert.tipo,
                'mensaje': alert.mensaje,
                'modulo': alert.modulo,
                'estado': alert.estado,
                'valor_actual': alert.valor_actual,
                'umbral': alert.umbral,
                'timestamp_resuelto': alert.timestamp_resuelto.isoformat() if alert.timestamp_resuelto else None,
            }
            result.append(item)

        return jsonify(result)
    except Exception as e:
        logger.exception(
        "event=alerts_query_failed priority=%s status=%s module=%s source_ip=%s",
        safe_log_value(locals().get("priority"), limit=32),
        safe_log_value(locals().get("status"), limit=32),
        safe_log_value(locals().get("modulo"), limit=32),
        request_source_ip(),
        )
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/<int:alert_id>', methods=['PUT'])
@auth_required(roles=["admin", "operador"])
def update_alert(alert_id):
    try:
        alert = Alerta.query.get_or_404(alert_id)
        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict):
            return jsonify({
                'error': 'Datos de alerta inválidos.'
            }), 400

        changed_fields = []

        if 'estado' in data:
            alert.estado = data['estado']
            changed_fields.append('estado')

            if data['estado'] == 'resolved':
                alert.timestamp_resuelto = datetime.utcnow()
                changed_fields.append('timestamp_resuelto')

        db.session.commit()

        if changed_fields:
            record_privileged_action(
                action='alert_updated',
                resource_type='alert',
                resource_id=alert.id,
                changed_fields=changed_fields,
            )

        return jsonify({'message': 'Alert updated successfully'})

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=alert_update_failed alert_id=%s source_ip=%s",
            alert_id,
            request_audit_source_ip(),
        )
        return jsonify({
            'error': 'Error interno al actualizar alerta.'
        }), 500


@app.route('/api/alerts/stats', methods=['GET'])
def get_alert_stats():
    """Get alert statistics"""
    try:
        critical = Alerta.query.filter_by(prioridad='critical', estado='active').count()
        warning = Alerta.query.filter_by(prioridad='warning', estado='active').count()
        info = Alerta.query.filter_by(prioridad='info', estado='active').count()
        resolved = Alerta.query.filter_by(estado='resolved').count()
        
        return jsonify({
            'critical': critical,
            'warning': warning,
            'info': info,
            'resolved': resolved
        })
    except Exception as e:
        logger.exception(
        "event=alert_stats_query_failed source_ip=%s",
        request_source_ip(),
        )
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/mark-all', methods=['PUT'])
@auth_required(roles=["admin", "operador"])
def mark_all_alerts():
    try:
        alerts = Alerta.query.filter_by(estado='active').all()

        for alert in alerts:
            alert.estado = 'acknowledged'

        db.session.commit()

        record_privileged_action(
            action='alerts_acknowledged_bulk',
            resource_type='alert',
            resource_id='all_active',
            changed_fields=['estado'],
            affected_count=len(alerts),
        )

        return jsonify({
            'message': f'Marked {len(alerts)} alerts as acknowledged'
        })

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=alerts_mark_all_failed source_ip=%s",
            request_audit_source_ip(),
        )
        return jsonify({
            'error': 'Error interno al reconocer alertas.'
        }), 500


@app.route('/api/alerts/all', methods=['DELETE'])
@auth_required(roles=["admin"])
def delete_all_alerts():
    try:
        num_deleted = db.session.query(Alerta).delete()
        db.session.commit()

        record_privileged_action(
            action='alerts_deleted_bulk',
            resource_type='alert',
            resource_id='all',
            affected_count=num_deleted,
        )

        return jsonify({
            'message': f'Se eliminaron {num_deleted} alertas correctamente'
        })

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=alerts_delete_all_failed source_ip=%s",
            request_audit_source_ip(),
        )
        return jsonify({
            'error': 'Error interno al eliminar alertas.'
        }), 500


@app.route('/api/alerts/check', methods=['POST'])
@auth_required(roles=["admin", "operador"])
def trigger_alert_check():
    try:
        check_and_create_alerts()

        record_privileged_action(
            action='alerts_check_triggered',
            resource_type='alert_engine',
            resource_id='manual_check',
        )

        return jsonify({
            'message': 'Alert check completed successfully'
        })

    except Exception:
        db.session.rollback()
        logger.exception(
            "event=alerts_check_failed source_ip=%s",
            request_audit_source_ip(),
        )
        return jsonify({
            'error': 'Error interno al ejecutar revisión de alertas.'
        }), 500


# -------------------------------------------------------
# Eventos de seguridad
# -------------------------------------------------------
@app.route("/api/security-events", methods=["GET"])
@auth_required(roles=["admin", "operador"])
def get_security_events():
    """Consultar eventos persistentes para análisis operativo."""

    severity = request.args.get("severity", "all").strip().lower()
    source = request.args.get("source", "all").strip().lower()
    module = request.args.get("module", "all").strip()
    limit = request.args.get("limit", 100, type=int)
    limit = max(1, min(limit, 200))

    query = SecurityEvent.query

    if severity != "all":
        query = query.filter(SecurityEvent.severity == severity)

    if source != "all":
        query = query.filter(SecurityEvent.source == source)

    if module != "all":
        query = query.filter(SecurityEvent.module == module)

    events = query.order_by(SecurityEvent.created_at.desc()).limit(limit).all()

    return jsonify({
        "count": len(events),
        "events": [
            serialize_security_event(event)
            for event in events
        ],
    }), 200


@app.route("/api/security-events/ingest", methods=["POST"])
def ingest_security_event():
    """Recibir eventos técnicos desde mqtt_subscriber con llave propia."""

    if not validate_security_events_key():
        logger.warning(
            "event=security_events_ingest_unauthorized source_ip=%s",
            request_source_ip(),
        )
        return jsonify({"error": "No autorizado."}), 401

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "JSON inválido o ausente."}), 400

    event_type = data.get("event_type", "")
    severity = data.get("severity", "warning")
    module = data.get("module")
    reason = data.get("reason")
    topic = data.get("topic")

    if not isinstance(event_type, str) or not re.fullmatch(
        r"[a-z][a-z0-9_]{2,99}",
        event_type,
    ):
        return jsonify({"error": "event_type inválido."}), 422

    normalized_severity = safe_log_value(severity, limit=20).lower()

    if normalized_severity not in SECURITY_EVENT_SEVERITIES:
        return jsonify({"error": "severity inválida."}), 422

    if module is not None and (
        not isinstance(module, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", module)
    ):
        return jsonify({"error": "module inválido."}), 422

    security_event = record_security_event(
        event_type=event_type,
        severity=normalized_severity,
        source="mqtt_subscriber",
        module=module,
        source_ip=request_source_ip(),
        reason=reason,
        topic=topic,
    )

    if not security_event:
        return jsonify({
            "error": "No fue posible guardar el evento."
        }), 500

    return jsonify({
        "msg": "Security event recorded.",
        "id": security_event.id,
    }), 201


# -------------------------------------------------------
# Granjas CRUD
# -------------------------------------------------------

@app.route('/api/granjas', methods=['GET'])
def get_granjas():
    try:
        granjas = Granja.query.all()
        result = []
        for g in granjas:
            result.append({
                'id': g.id,
                'nombre': g.nombre,
                'ubicacion': g.ubicacion,
                'naves': [{'id': n.id, 'nombre': n.nombre} for n in g.naves]
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/granjas', methods=['POST'])
@auth_required(roles=["admin"])
def create_granja():
    try:
        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict) or not data.get('nombre'):
            return jsonify({'error': 'nombre is required'}), 400

        granja = Granja(
            nombre=data['nombre'],
            ubicacion=data.get('ubicacion'),
        )
        db.session.add(granja)
        db.session.commit()

        changed_fields = ['nombre']

        if 'ubicacion' in data:
            changed_fields.append('ubicacion')

        record_privileged_action(
            action='farm_created',
            resource_type='farm',
            resource_id=granja.id,
            changed_fields=changed_fields,
        )

        return jsonify({
            'id': granja.id,
            'nombre': granja.nombre
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/granjas/<int:granja_id>', methods=['PUT'])
@auth_required(roles=["admin"])
def update_granja(granja_id):
    try:
        granja = Granja.query.get_or_404(granja_id)
        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict):
            return jsonify({'error': 'Datos de granja inválidos'}), 400

        changed_fields = []

        if 'nombre' in data:
            granja.nombre = data['nombre']
            changed_fields.append('nombre')

        if 'ubicacion' in data:
            granja.ubicacion = data['ubicacion']
            changed_fields.append('ubicacion')

        db.session.commit()

        if changed_fields:
            record_privileged_action(
                action='farm_updated',
                resource_type='farm',
                resource_id=granja.id,
                changed_fields=changed_fields,
            )

        return jsonify({'msg': 'Granja actualizada'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/granjas/<int:granja_id>', methods=['DELETE'])
@auth_required(roles=["admin"])
def delete_granja(granja_id):
    try:
        granja = Granja.query.get_or_404(granja_id)

        if granja.naves:
            return jsonify({
                'error': 'No se puede eliminar: tiene naves asociadas'
            }), 409

        deleted_id = granja.id
        db.session.delete(granja)
        db.session.commit()

        record_privileged_action(
            action='farm_deleted',
            resource_type='farm',
            resource_id=deleted_id,
        )

        return jsonify({'msg': 'Granja eliminada'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# -------------------------------------------------------
# Naves CRUD
# -------------------------------------------------------

@app.route('/api/naves', methods=['GET'])
def get_naves():
    try:
        granja_id = request.args.get('granja_id', type=int)
        query = Nave.query
        if granja_id:
            query = query.filter_by(granja_id=granja_id)
        naves = query.all()
        result = []
        for n in naves:
            parvada = calcular_semana_parvada(n.fecha_inicio_parvada)
            result.append({
                'id': n.id,
                'nombre': n.nombre,
                'granja_id': n.granja_id,
                'granja_nombre': n.granja.nombre if n.granja else None,
                'fecha_inicio_parvada': n.fecha_inicio_parvada.isoformat() if n.fecha_inicio_parvada else None,
                'parvada': parvada,
                'modulos': [{'id': m.id, 'codigo': m.codigo} for m in n.modulos]
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/naves', methods=['POST'])
@auth_required(roles=["admin"])
def create_nave():
    try:
        data = request.get_json(silent=True) or {}

        if (
            not isinstance(data, dict)
            or not data.get('nombre')
            or not data.get('granja_id')
        ):
            return jsonify({
                'error': 'nombre y granja_id son requeridos'
            }), 400

        fecha = None

        if data.get('fecha_inicio_parvada'):
            from datetime import date as date_type
            fecha = date_type.fromisoformat(data['fecha_inicio_parvada'])

        nave = Nave(
            nombre=data['nombre'],
            granja_id=data['granja_id'],
            fecha_inicio_parvada=fecha,
        )
        db.session.add(nave)
        db.session.commit()

        changed_fields = ['nombre', 'granja_id']

        if 'fecha_inicio_parvada' in data:
            changed_fields.append('fecha_inicio_parvada')

        record_privileged_action(
            action='house_created',
            resource_type='house',
            resource_id=nave.id,
            changed_fields=changed_fields,
            parent_farm_id=nave.granja_id,
        )

        return jsonify({
            'id': nave.id,
            'nombre': nave.nombre
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/naves/<int:nave_id>', methods=['PUT'])
@auth_required(roles=["admin"])
def update_nave(nave_id):
    try:
        nave = Nave.query.get_or_404(nave_id)
        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict):
            return jsonify({'error': 'Datos de nave inválidos'}), 400

        changed_fields = []

        if 'nombre' in data:
            nave.nombre = data['nombre']
            changed_fields.append('nombre')

        if 'granja_id' in data:
            nave.granja_id = data['granja_id']
            changed_fields.append('granja_id')

        if 'fecha_inicio_parvada' in data:
            from datetime import date as date_type

            nave.fecha_inicio_parvada = (
                date_type.fromisoformat(data['fecha_inicio_parvada'])
                if data['fecha_inicio_parvada']
                else None
            )
            changed_fields.append('fecha_inicio_parvada')

        db.session.commit()

        if changed_fields:
            record_privileged_action(
                action='house_updated',
                resource_type='house',
                resource_id=nave.id,
                changed_fields=changed_fields,
                parent_farm_id=nave.granja_id,
            )

        return jsonify({'msg': 'Nave actualizada'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/naves/<int:nave_id>', methods=['DELETE'])
@auth_required(roles=["admin"])
def delete_nave(nave_id):
    try:
        nave = Nave.query.get_or_404(nave_id)

        if nave.modulos:
            return jsonify({
                'error': 'No se puede eliminar: tiene módulos asociados'
            }), 409

        deleted_id = nave.id
        parent_farm_id = nave.granja_id

        db.session.delete(nave)
        db.session.commit()

        record_privileged_action(
            action='house_deleted',
            resource_type='house',
            resource_id=deleted_id,
            parent_farm_id=parent_farm_id,
        )

        return jsonify({'msg': 'Nave eliminada'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# -------------------------------------------------------
# Módulos registry
# -------------------------------------------------------

@app.route('/api/modulos', methods=['GET'])
def get_modulos():
    try:
        # Módulos registrados en la tabla
        registered = {m.codigo: m for m in Modulo.query.all()}
        # Módulos vistos en lecturas + su última lectura
        seen_rows = db.session.query(
            Lectura.modulo,
            db.func.max(Lectura.hora).label('ultima')
        ).group_by(Lectura.modulo).all()
        seen_codigos = {row.modulo: row.ultima for row in seen_rows}
        # Unión de ambos
        all_codigos = set(registered.keys()) | set(seen_codigos.keys())
        result = []
        for codigo in sorted(all_codigos):
            m = registered.get(codigo)
            nave = None
            granja = None
            if m and m.nave_id:
                n = Nave.query.get(m.nave_id)
                if n:
                    nave = {'id': n.id, 'nombre': n.nombre}
                    granja = {'id': n.granja.id, 'nombre': n.granja.nombre} if n.granja else None
            ultima = seen_codigos.get(codigo)
            result.append({
                'codigo': codigo,
                'nombre': m.nombre if m else None,
                'nave_id': m.nave_id if m else None,
                'nave': nave,
                'granja': granja,
                'ultima_lectura': ultima.isoformat() if ultima else None
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/modulos/<string:codigo>', methods=['PUT'])
@auth_required(roles=["admin", "operador"])
def update_modulo(codigo):
    try:
        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict):
            return jsonify({'error': 'Datos de módulo inválidos'}), 400

        modulo = Modulo.query.filter_by(codigo=codigo).first()
        created = modulo is None

        if created:
            modulo = Modulo(codigo=codigo)
            db.session.add(modulo)

        changed_fields = ['codigo'] if created else []

        if 'nave_id' in data:
            modulo.nave_id = data['nave_id']
            changed_fields.append('nave_id')

        if 'nombre' in data:
            modulo.nombre = data['nombre']
            changed_fields.append('nombre')

        db.session.commit()

        if changed_fields:
            record_privileged_action(
                action='module_created' if created else 'module_updated',
                resource_type='module',
                resource_id=codigo,
                changed_fields=changed_fields,
                parent_house_id=modulo.nave_id,
            )

        return jsonify({
            'msg': 'Módulo actualizado',
            'codigo': codigo
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# -------------------------------------------------------
# Parvada lookup
# -------------------------------------------------------

@app.route('/api/parvada/<string:modulo_codigo>', methods=['GET'])
@limiter.exempt
def get_parvada(modulo_codigo):
    try:
        m = Modulo.query.filter_by(codigo=modulo_codigo).first()
        if not m or not m.nave_id:
            return jsonify({'parvada': None, 'nave': None, 'granja': None})
        nave = Nave.query.get(m.nave_id)
        if not nave:
            return jsonify({'parvada': None, 'nave': None, 'granja': None})
        parvada = calcular_semana_parvada(nave.fecha_inicio_parvada)
        granja_data = None
        if nave.granja:
            granja_data = {'id': nave.granja.id, 'nombre': nave.granja.nombre, 'ubicacion': nave.granja.ubicacion}
        return jsonify({
            'parvada': parvada,
            'nave': {'id': nave.id, 'nombre': nave.nombre},
            'granja': granja_data
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def start(port=5000, host='0.0.0.0'):
    debug_mode = os.getenv("FLASK_ENV", "production").lower() == "development"
    app.run(debug=debug_mode, port=port, host=host, use_reloader=False)

if __name__ == '__main__':
    start()
