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

jwt_secret_key = os.getenv("JWT_SECRET_KEY")
if not jwt_secret_key or len(jwt_secret_key) < 32:
    raise RuntimeError("JWT_SECRET_KEY no definida o demasiado corta. Debe tener al menos 32 caracteres.")

jwt_access_token_expires_minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "60"))

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
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(120))
    role = db.Column(db.String(50))
    initials = db.Column(db.String(10))
    profile_image_url = db.Column(db.String(500))
    
    def set_password(self, password):
        """Hash the password and store it"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if the provided password matches the hash"""
        return check_password_hash(self.password_hash, password)

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

def get_current_user_from_token():
    """Return authenticated user from Bearer token or an error response."""
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
@limiter.limit("10 per minute")  # Brute-force protection
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

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            access_token = generate_jwt_token(user)
            logger.info(
                "event=login_succeeded user_id=%s username=%s role=%s source_ip=%s",
                user.id,
                safe_log_value(user.username, limit=64),
                safe_log_value(user.role, limit=32),
                request_source_ip(),
            )

            return jsonify({
                "msg": "Login successful",
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": jwt_access_token_expires_minutes * 60,
                "user": serialize_user(user)
            }), 200

        logger.warning(
            "event=login_failed username=%s source_ip=%s",
            safe_log_value(username, limit=64),
            request_source_ip(),
        )
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
        
        if request.method == 'PUT':
            current_role = normalize_role(request.current_user.role)
            if current_role != "admin":
                return jsonify({
                    "error": "Permisos insuficientes.",
                    "details": ["Solo un administrador puede modificar usuarios."]
                }), 403
            data = request.get_json()
            if 'full_name' in data: user.full_name = data['full_name']
            if 'role' in data: user.role = data['role']
            if 'initials' in data: user.initials = data['initials']
            if 'profile_image_url' in data: user.profile_image_url = data['profile_image_url']
            
            db.session.commit()
            return jsonify({'msg': 'User updated successfully'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        data = request.get_json()
        
        for item in data:
            variable    = item['variable']
            valor_medio = float(item['valor_medio'])
            valor_alto  = float(item['valor_alto'])
            valor_grave = float(item['valor_grave'])

            if not (valor_medio < valor_alto < valor_grave):
                return jsonify({
                    'error': f"Umbrales inválidos para '{variable}': "
                             f"debe cumplirse medio ({valor_medio}) < alto ({valor_alto}) < grave ({valor_grave})"
                }), 400

            # Update or create
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
                    valor_grave=valor_grave
                )
                db.session.add(umbral)
        
        db.session.commit()
        logger.info(
            "event=thresholds_updated actor_user_id=%s actor_role=%s "
            "updated_count=%s source_ip=%s",
            request.current_user.id,
            safe_log_value(request.current_user.role, limit=32),
            len(data),
            request_source_ip(),
        )
        return jsonify({'message': 'Thresholds updated successfully'}), 200
    except Exception:
        db.session.rollback()
        logger.exception(
            "event=thresholds_update_failed actor_user_id=%s source_ip=%s",
            getattr(getattr(request, "current_user", None), "id", "-"),
            request_source_ip(),
        )
        return jsonify({'error': 'Error interno al actualizar umbrales.'}), 500

@app.route('/api/umbrales/init', methods=['POST'])
@auth_required(roles=["admin"])
def init_umbrales():
    """Initialize default thresholds only if they don't exist"""
    try:
        # Check if thresholds already exist
        existing_count = Umbral.query.count()
        if existing_count > 0:
            return jsonify({
                'message': f'Thresholds already exist ({existing_count} records)',
                'action': 'none'
            }), 200
        
        # Default thresholds
        default_umbrales = [
            {'variable': 'temperatura', 'valor_medio': 25.0, 'valor_alto': 30.0, 'valor_grave': 35.0},
            {'variable': 'humedad', 'valor_medio': 60.0, 'valor_alto': 75.0, 'valor_grave': 85.0},
            {'variable': 'amoniaco', 'valor_medio': 20.0, 'valor_alto': 30.0, 'valor_grave': 40.0},
            {'variable': 'co2', 'valor_medio': 1000.0, 'valor_alto': 1500.0, 'valor_grave': 2000.0},
            {'variable': 'co', 'valor_medio': 10.0, 'valor_alto': 20.0, 'valor_grave': 30.0}
        ]
        
        for item in default_umbrales:
            umbral = Umbral(
                variable=item['variable'],
                valor_medio=item['valor_medio'],
                valor_alto=item['valor_alto'],
                valor_grave=item['valor_grave']
            )
            db.session.add(umbral)
        
        db.session.commit()
        return jsonify({
            'message': f'Default thresholds created ({len(default_umbrales)} records)',
            'action': 'created',
            'count': len(default_umbrales)
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500



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
    """Update alert status"""
    try:
        alert = Alerta.query.get_or_404(alert_id)
        data = request.get_json()
        
        if 'estado' in data:
            alert.estado = data['estado']
            if data['estado'] == 'resolved':
                alert.timestamp_resuelto = datetime.utcnow()
        
        db.session.commit()
        return jsonify({'message': 'Alert updated successfully'})
    except Exception as e:
        logger.exception(
        "event=alert_update_failed alert_id=%s source_ip=%s",
        alert_id,
        request_source_ip(),
        )
        return jsonify({'error': str(e)}), 500

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
    """Mark all active alerts as acknowledged"""
    try:
        alerts = Alerta.query.filter_by(estado='active').all()
        for alert in alerts:
            alert.estado = 'acknowledged'
        
        db.session.commit()
        return jsonify({'message': f'Marked {len(alerts)} alerts as acknowledged'})
    except Exception as e:
        logger.exception(
        "event=alerts_mark_all_failed source_ip=%s",
        request_source_ip(),
        )
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/all', methods=['DELETE'])
@auth_required(roles=["admin"])
def delete_all_alerts():
    """Delete all alerts"""
    try:
        num_deleted = db.session.query(Alerta).delete()
        db.session.commit()
        return jsonify({'message': f'Se eliminaron {num_deleted} alertas correctamente'})
    except Exception as e:
        logger.exception(
        "event=alerts_delete_all_failed source_ip=%s",
        request_source_ip(),
        )
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/check', methods=['POST'])
@auth_required(roles=["admin", "operador"])
def trigger_alert_check():
    """Manually trigger alert creation from existing data"""
    try:
        check_and_create_alerts()
        return jsonify({'message': 'Alert check completed successfully'})
    except Exception as e:
        logger.exception(
        "event=alerts_check_failed source_ip=%s",
        request_source_ip(),
        )
        return jsonify({'error': str(e)}), 500

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
        data = request.get_json()
        if not data or not data.get('nombre'):
            return jsonify({'error': 'nombre is required'}), 400
        granja = Granja(nombre=data['nombre'], ubicacion=data.get('ubicacion'))
        db.session.add(granja)
        db.session.commit()
        return jsonify({'id': granja.id, 'nombre': granja.nombre}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/granjas/<int:granja_id>', methods=['PUT'])
@auth_required(roles=["admin"])
def update_granja(granja_id):
    try:
        granja = Granja.query.get_or_404(granja_id)
        data = request.get_json()
        if 'nombre' in data:
            granja.nombre = data['nombre']
        if 'ubicacion' in data:
            granja.ubicacion = data['ubicacion']
        db.session.commit()
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
            return jsonify({'error': 'No se puede eliminar: tiene naves asociadas'}), 409
        db.session.delete(granja)
        db.session.commit()
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
        data = request.get_json()
        if not data or not data.get('nombre') or not data.get('granja_id'):
            return jsonify({'error': 'nombre y granja_id son requeridos'}), 400
        fecha = None
        if data.get('fecha_inicio_parvada'):
            from datetime import date as date_type
            fecha = date_type.fromisoformat(data['fecha_inicio_parvada'])
        nave = Nave(
            nombre=data['nombre'],
            granja_id=data['granja_id'],
            fecha_inicio_parvada=fecha
        )
        db.session.add(nave)
        db.session.commit()
        return jsonify({'id': nave.id, 'nombre': nave.nombre}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/naves/<int:nave_id>', methods=['PUT'])
@auth_required(roles=["admin"])
def update_nave(nave_id):
    try:
        nave = Nave.query.get_or_404(nave_id)
        data = request.get_json()
        if 'nombre' in data:
            nave.nombre = data['nombre']
        if 'granja_id' in data:
            nave.granja_id = data['granja_id']
        if 'fecha_inicio_parvada' in data:
            if data['fecha_inicio_parvada']:
                from datetime import date as date_type
                nave.fecha_inicio_parvada = date_type.fromisoformat(data['fecha_inicio_parvada'])
            else:
                nave.fecha_inicio_parvada = None
        db.session.commit()
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
            return jsonify({'error': 'No se puede eliminar: tiene módulos asociados'}), 409
        db.session.delete(nave)
        db.session.commit()
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
        data = request.get_json()
        m = Modulo.query.filter_by(codigo=codigo).first()
        if not m:
            m = Modulo(codigo=codigo)
            db.session.add(m)
        if 'nave_id' in data:
            m.nave_id = data['nave_id']
        if 'nombre' in data:
            m.nombre = data['nombre']
        db.session.commit()
        return jsonify({'msg': 'Módulo actualizado', 'codigo': codigo})
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
