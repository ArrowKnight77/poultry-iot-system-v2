from flask import Flask, render_template, request, redirect, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from flask_cors import CORS, cross_origin
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
import requests
import json
from datetime import datetime



from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Security: Rate Limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri="memory://"
)

@app.after_request
def add_security_headers(response):
    """Add security headers to every response"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

db = SQLAlchemy(app)

PASSWORD_HASH_METHOD = "scrypt"
PASSWORD_HASH_SALT_LENGTH = 16
SUPPORTED_PASSWORD_HASH_PREFIXES = ("scrypt:", "pbkdf2:")
CANONICAL_ROLES = ("admin", "operador", "visor")
DEFAULT_USER_ROLE = "visor"
ROLE_ALIASES = {
    "admin": "admin",
    "administrator": "admin",
    "administrador": "admin",
    "operador": "operador",
    "operator": "operador",
    "visor": "visor",
    "viewer": "visor",
    "user": "visor",
    "usuario": "visor",
}


def generate_secure_password_hash(password):
    return generate_password_hash(
        password,
        method=PASSWORD_HASH_METHOD,
        salt_length=PASSWORD_HASH_SALT_LENGTH,
    )


def is_supported_password_hash(password_hash):
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
    if not is_supported_password_hash(password_hash):
        return False

    try:
        return check_password_hash(password_hash, password)
    except (TypeError, ValueError):
        return False


def normalize_role(role):
    raw_role = (role or "").strip().casefold()

    if not raw_role:
        return DEFAULT_USER_ROLE

    return ROLE_ALIASES.get(raw_role, DEFAULT_USER_ROLE)


dashboard_cors_origins_raw = os.getenv(
    "DASHBOARD_CORS_ORIGINS",
    "http://localhost:5000,http://localhost:5001"
)

dashboard_cors_origins = [
    origin.strip()
    for origin in dashboard_cors_origins_raw.split(",")
    if origin.strip()
]

CORS(
    app,
    origins=dashboard_cors_origins,
    supports_credentials=True
)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# Importar modelo User de la API (tabla 'users')
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)
    full_name = db.Column(db.String(120))
    role = db.Column(
        db.String(20),
        nullable=False,
        default=DEFAULT_USER_ROLE,
    )
    initials = db.Column(db.String(10))
    profile_image_url = db.Column(db.String(500))
    mfa_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )
    mfa_secret_encrypted = db.Column(db.String(512))
    mfa_enrolled_at = db.Column(db.DateTime)
    mfa_last_verified_at = db.Column(db.DateTime)
    
    def set_password(self, password):
        """Hash the password and store it."""
        self.password_hash = generate_secure_password_hash(password)
    
    def check_password(self, password):
        """Check if the provided password matches a supported hash."""
        return verify_password_hash(self.password_hash, password)

    def normalized_role(self):
        return normalize_role(self.role)

# No crear usuarios automáticamente
# El primer usuario debe registrarse manualmente
try:
    with app.app_context():
        # Verificar si la tabla existe y hay usuarios
        User.query.first()
except Exception as e:
    print(f"Base de datos no inicializada aún: {e}")
    pass  # La API creará las tablas al iniciar

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_live_data():
    """Obtiene datos en tiempo real desde la API del dashboard"""
    try:
        # Usar API_BASE_URL para endpoints del dashboard
        api_url = os.getenv('API_BASE_URL', 'http://localhost:5000')
        
        # Agregar timestamp para evitar cacheo
        import time
        timestamp = int(time.time())
        url = f'{api_url}/api/live-data?t={timestamp}'
        
        response = requests.get(url, timeout=5)
        print(f"🔍 Estado API Dashboard: {response.status_code} - URL: {url}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"📊 Datos recibidos del API: {data}")
            
            # Verifica que sea un objeto válido
            if (data and isinstance(data, dict) and 
                'temperatura' in data and 'humedad' in data):
                print(f"✅ Datos válidos - Temp: {data.get('temperatura')}, Hum: {data.get('humedad')}, Timestamp: {data.get('timestamp')}")
                return data
            else:
                print("⚠️ Datos inválidos recibidos - campos faltantes")
                return None
        else:
            print(f"❌ Error HTTP: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Error obteniendo datos del API: {e}")
        return None

def get_historical_data_from_api(range_param, from_date=None, to_date=None, house_param=None):
    """Obtiene datos históricos desde la API principal"""
    try:
        api_url = os.getenv('API_BASE_URL', 'http://localhost:5000')
        url = f'{api_url}/api/historical?range={range_param}'
        if from_date and to_date:
            url += f'&from={from_date}&to={to_date}'
        if house_param:
            url += f'&house={house_param}'
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Error HTTP obteniendo histórico: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error obteniendo datos históricos: {e}")
        return None

def get_umbrales_from_api():
    """Obtiene umbrales desde la API principal"""
    try:
        api_url = os.getenv('API_BASE_URL', 'http://localhost:5000')
        url = f'{api_url}/api/umbrales'
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"❌ Error obteniendo umbrales: {e}")
        return []

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Usuario y contraseña son requeridos.")
            return render_template("login.html")

        api_url = os.getenv("API_BASE_URL", "http://api:5000").rstrip("/")

        try:
            api_response = requests.post(
                f"{api_url}/api/login",
                json={
                    "username": username,
                    "password": password,
                },
                timeout=5,
            )
        except requests.RequestException as exc:
            app.logger.warning(
                "dashboard_login_api_unavailable error_type=%s",
                type(exc).__name__,
            )
            flash("No fue posible validar las credenciales. Intente nuevamente.")
            return render_template("login.html")

        try:
            response_data = api_response.json()
        except ValueError:
            response_data = {}

        if api_response.status_code == 200:
            api_user = response_data.get("user", {})
            user_id = api_user.get("id") if isinstance(api_user, dict) else None
            user = db.session.get(User, user_id) if user_id else None

            if not user:
                app.logger.error(
                    "dashboard_login_user_not_found user_id=%s",
                    user_id,
                )
                flash("No fue posible iniciar la sesión. Contacte al administrador.")
                return render_template("login.html")

            # La API ya validó credenciales, bloqueo y contador persistente.
            login_user(user)
            return redirect("/dashboard")

        if api_response.status_code == 429:
            raw_retry_after = response_data.get(
                "retry_after_seconds",
                api_response.headers.get("Retry-After", "60"),
            )

            try:
                retry_after_seconds = int(raw_retry_after)
            except (TypeError, ValueError):
                retry_after_seconds = 60

            retry_after_seconds = min(max(retry_after_seconds, 1), 86400)

            return render_template(
                "login.html",
                lockout_seconds=retry_after_seconds,
            )

        if api_response.status_code in (400, 401):
            flash("Usuario o contraseña incorrectos")
        else:
            app.logger.warning(
                "dashboard_login_api_error status=%s",
                api_response.status_code,
            )
            flash("Error en el sistema. Intente nuevamente.")

    return render_template("login.html")


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per hour")
def register():
    if request.method == 'POST':
        try:
            # Verificar si ya hay usuarios
            user_count = User.query.count()
            if user_count > 0:
                flash('Ya existen usuarios registrados. Use el formulario de login.')
                return redirect('/login')
            
            # Validar contraseñas coinciden
            password = request.form['password']
            confirm_password = request.form['confirm_password']
            
            if password != confirm_password:
                flash('Las contraseñas no coinciden')
                return render_template('login.html', show_register=True)
            
            if len(password) < 6:
                flash('La contraseña debe tener al menos 6 caracteres')
                return render_template('login.html', show_register=True)
            
            # Crear usuario
            username = request.form['username']
            full_name = request.form['full_name']
            role = normalize_role(request.form.get('role'))
            
            # Generar iniciales
            initials = ''.join([n[0].upper() for n in full_name.split()])[:2]
            
            new_user = User(
                username=username,
                full_name=full_name,
                role=role,
                initials=initials
            )
            new_user.set_password(password)
            
            db.session.add(new_user)
            db.session.commit()
            
            flash('Usuario registrado exitosamente. Ahora puede iniciar sesión.')
            return redirect('/login')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar usuario: {str(e)}')
            print(f"Error en registro: {e}")
            return render_template('login.html', show_register=True)
    
    # Verificar si ya hay usuarios
    try:
        user_count = User.query.count()
        if user_count > 0:
            flash('Ya existen usuarios registrados. Use el formulario de login.')
            return redirect('/login')
    except:
        flash('Base de datos no disponible. Espere a que el sistema inicie completamente.')
        return redirect('/login')
    
    return render_template('login.html', show_register=True)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')

#pestañas de dashboard

@app.route('/dashboard')
@login_required
def dashboard():
    # Verificar si hay usuarios registrados
    try:
        user_count = User.query.count()
        if user_count == 0:
            flash('No hay usuarios registrados. Contacte al administrador.')
            return redirect('/login')
    except:
        flash('Base de datos no disponible. Espere a que el sistema inicie completamente.')
        return redirect('/login')
    
    # DEBUG: Verificar estado del usuario actual
    print(f"DEBUG: current_user is_authenticated: {current_user.is_authenticated}")
    print(f"DEBUG: current_user id: {getattr(current_user, 'id', 'None')}")
    print(f"DEBUG: current_user username: {getattr(current_user, 'username', 'None')}")
    
    # Pasar datos del usuario a la plantilla
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.normalized_role(),
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    
    print(f"DEBUG: user_data being passed: {user_data}")
    
    return render_template('dashboard.html', user_data=user_data)


@app.route('/api/historical')
@login_required
def api_historical():
    """Endpoint proxy para datos históricos - funciona desde cualquier dispositivo"""
    try:
        range_param = request.args.get('range', '24h')
        from_date = request.args.get('from', None)
        to_date = request.args.get('to', None)
        house_param = request.args.get('house', None)
        
        data = get_historical_data_from_api(range_param, from_date, to_date, house_param)
        
        if data:
            return jsonify(data)
        else:
            return jsonify({
                "timestamps": [],
                "house": [],
                "temperature": [],
                "humidity": [],
                "ammonia": [],
                "co": [],
                "co2": [],
                "oxygen": [],
                "error": "No hay datos disponibles"
            })
            
    except Exception as e:
        print(f"Error api_historical: {e}")
        return jsonify({
            "timestamps": [],
            "house": [],
            "temperature": [],
            "humidity": [],
            "ammonia": [],
            "co": [],
            "co2": [],
            "oxygen": [],
            "error": str(e)
        }), 500

@app.route('/api/live-data')
@login_required
def api_live_data():
    """Proxy autenticado para datos en tiempo real."""
    try:
        r = requests.get(
            f'{_api_url()}/api/live-data',
            params=request.args.to_dict(),
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/umbrales', methods=['GET', 'POST'])
@login_required
def api_umbrales():
    """Proxy autenticado para consultar y actualizar umbrales."""
    try:
        if request.method == 'GET':
            r = requests.get(
                f'{_api_url()}/api/umbrales',
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        else:
            r = requests.post(
                f'{_api_url()}/api/umbrales',
                json=request.get_json(silent=True),
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )

        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _api_url():
    return os.getenv('API_BASE_URL', 'http://localhost:5000').rstrip('/')


def _dashboard_proxy_headers():
    """Headers internos para que la API identifique al usuario real."""
    proxy_key = os.getenv("DASHBOARD_PROXY_KEY", "")

    if not proxy_key:
        app.logger.error("dashboard_proxy_key_missing")

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    source_ip = (
        forwarded_for.split(",", 1)[0].strip()
        if forwarded_for
        else request.remote_addr
    )

    return {
        "X-Dashboard-Proxy-Key": proxy_key,
        "X-Dashboard-User-Id": str(current_user.id),
        "X-Dashboard-Source-IP": source_ip or "",
    }


def _proxy_json_response(response):
    try:
        return jsonify(response.json()), response.status_code
    except ValueError:
        return jsonify({
            'error': 'Respuesta inválida desde API interna.'
        }), response.status_code


@app.route('/dashboard-api/user/<int:user_id>', methods=['GET', 'PUT'])
@app.route('/api/user/<int:user_id>', methods=['GET', 'PUT'])
@login_required
def proxy_user(user_id):
    try:
        if request.method == 'GET':
            r = requests.get(
                f'{_api_url()}/api/user/{user_id}',
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        else:
            r = requests.put(
                f'{_api_url()}/api/user/{user_id}',
                json=request.get_json(silent=True),
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )

        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alerts', methods=['GET'])
@login_required
def proxy_alerts():
    try:
        r = requests.get(
            f'{_api_url()}/api/alerts',
            params=request.args.to_dict(),
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alerts/stats', methods=['GET'])
@login_required
def proxy_alert_stats():
    try:
        r = requests.get(
            f'{_api_url()}/api/alerts/stats',
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alerts/<int:alert_id>', methods=['PUT'])
@login_required
def proxy_alert(alert_id):
    try:
        r = requests.put(
            f'{_api_url()}/api/alerts/{alert_id}',
            json=request.get_json(silent=True),
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alerts/mark-all', methods=['PUT'])
@login_required
def proxy_mark_all_alerts():
    try:
        r = requests.put(
            f'{_api_url()}/api/alerts/mark-all',
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alerts/all', methods=['DELETE'])
@login_required
def proxy_delete_all_alerts():
    try:
        r = requests.delete(
            f'{_api_url()}/api/alerts/all',
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alerts/check', methods=['POST'])
@login_required
def proxy_alert_check():
    try:
        r = requests.post(
            f'{_api_url()}/api/alerts/check',
            headers=_dashboard_proxy_headers(),
            timeout=10,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/granjas', methods=['GET', 'POST'])
@login_required
def proxy_granjas():
    try:
        if request.method == 'GET':
            r = requests.get(
                f'{_api_url()}/api/granjas',
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        else:
            r = requests.post(
                f'{_api_url()}/api/granjas',
                json=request.get_json(silent=True),
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/granjas/<int:granja_id>', methods=['PUT', 'DELETE'])
@login_required
def proxy_granja(granja_id):
    try:
        if request.method == 'PUT':
            r = requests.put(
                f'{_api_url()}/api/granjas/{granja_id}',
                json=request.get_json(silent=True),
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        else:
            r = requests.delete(
                f'{_api_url()}/api/granjas/{granja_id}',
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/naves', methods=['GET', 'POST'])
@login_required
def proxy_naves():
    try:
        if request.method == 'GET':
            r = requests.get(
                f'{_api_url()}/api/naves',
                params=request.args.to_dict(),
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        else:
            r = requests.post(
                f'{_api_url()}/api/naves',
                json=request.get_json(silent=True),
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/naves/<int:nave_id>', methods=['PUT', 'DELETE'])
@login_required
def proxy_nave(nave_id):
    try:
        if request.method == 'PUT':
            r = requests.put(
                f'{_api_url()}/api/naves/{nave_id}',
                json=request.get_json(silent=True),
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        else:
            r = requests.delete(
                f'{_api_url()}/api/naves/{nave_id}',
                headers=_dashboard_proxy_headers(),
                timeout=5,
            )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/modulos', methods=['GET'])
@login_required
def proxy_modulos():
    try:
        r = requests.get(
            f'{_api_url()}/api/modulos',
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/modulos/<string:codigo>', methods=['PUT'])
@login_required
def proxy_modulo(codigo):
    try:
        r = requests.put(
            f'{_api_url()}/api/modulos/{codigo}',
            json=request.get_json(silent=True),
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/parvada/<string:modulo>')
@login_required
def proxy_parvada(modulo):
    try:
        r = requests.get(
            f'{_api_url()}/api/parvada/{modulo}',
            headers=_dashboard_proxy_headers(),
            timeout=5,
        )
        return _proxy_json_response(r)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


#Redirigir la raíz '/' al login
@app.route('/')
def index():
    return redirect('/login')

@app.route('/historical')
@login_required
def historical():
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.normalized_role(),
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    return render_template('historical.html', active='historical', user_data=user_data)

@app.route('/analysis')
@login_required
def analysis():
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.normalized_role(),
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    return render_template('analysis.html', active='analysis', user_data=user_data)

@app.route('/alerts')
@login_required
def alerts():
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.normalized_role(),
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    return render_template('alerts.html', active='alerts', user_data=user_data)

@app.route('/devices')
@login_required
def devices():
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.normalized_role(),
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    return render_template('devices.html', active='devices', user_data=user_data)

@app.route('/ml_models')
@login_required
def ml_models():
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.normalized_role(),
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    return render_template('ml_models.html', active='ml_models', user_data=user_data)

@app.route('/reports')
@login_required
def reports():
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.normalized_role(),
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    return render_template('reports.html', active='reports', user_data=user_data)

# En dashboard_avicola.py, modifica la función start:
def start(port=5001, host='0.0.0.0'):
    if not os.path.exists('templates'):
        os.makedirs('templates')
    debug_mode = os.getenv("FLASK_ENV", "development").lower() == "development"
    app.run(debug=debug_mode, port=port, host=host, use_reloader=False)


if __name__ == '__main__':
    start()
