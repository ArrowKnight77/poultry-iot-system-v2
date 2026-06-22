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
        try:
            user = User.query.filter_by(username=request.form['username']).first()
            if user and user.check_password(request.form['password']):
                login_user(user)
                return redirect('/dashboard')
            flash('Usuario o contraseña incorrectos')
        except Exception as e:
            flash('Error en el sistema. Intente nuevamente.')
            print(f"Error en login: {e}")
    
    return render_template('login.html')

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
            role = request.form.get('role', 'User')
            
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
        'role': current_user.role,
        'initials': current_user.initials,
        'profile_image_url': current_user.profile_image_url
    }
    
    print(f"DEBUG: user_data being passed: {user_data}")
    
    return render_template('dashboard.html', user_data=user_data)


@app.route('/api/historical')
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

@app.route('/api/umbrales')
def api_umbrales():
    """Endpoint proxy para umbrales"""
    try:
        data = get_umbrales_from_api()
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _api_url():
    return os.getenv('API_BASE_URL', 'http://localhost:5000')

@app.route('/api/granjas', methods=['GET', 'POST'])
@login_required
def proxy_granjas():
    try:
        if request.method == 'GET':
            r = requests.get(f'{_api_url()}/api/granjas', timeout=5)
        else:
            r = requests.post(f'{_api_url()}/api/granjas', json=request.get_json(), timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/granjas/<int:granja_id>', methods=['PUT', 'DELETE'])
@login_required
def proxy_granja(granja_id):
    try:
        if request.method == 'PUT':
            r = requests.put(f'{_api_url()}/api/granjas/{granja_id}', json=request.get_json(), timeout=5)
        else:
            r = requests.delete(f'{_api_url()}/api/granjas/{granja_id}', timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/naves', methods=['GET', 'POST'])
@login_required
def proxy_naves():
    try:
        if request.method == 'GET':
            params = request.args.to_dict()
            r = requests.get(f'{_api_url()}/api/naves', params=params, timeout=5)
        else:
            r = requests.post(f'{_api_url()}/api/naves', json=request.get_json(), timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/naves/<int:nave_id>', methods=['PUT', 'DELETE'])
@login_required
def proxy_nave(nave_id):
    try:
        if request.method == 'PUT':
            r = requests.put(f'{_api_url()}/api/naves/{nave_id}', json=request.get_json(), timeout=5)
        else:
            r = requests.delete(f'{_api_url()}/api/naves/{nave_id}', timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/modulos', methods=['GET'])
@login_required
def proxy_modulos():
    try:
        r = requests.get(f'{_api_url()}/api/modulos', timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/modulos/<string:codigo>', methods=['PUT'])
@login_required
def proxy_modulo(codigo):
    try:
        r = requests.put(f'{_api_url()}/api/modulos/{codigo}', json=request.get_json(), timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/parvada/<string:modulo>')
@login_required
def proxy_parvada(modulo):
    try:
        r = requests.get(f'{_api_url()}/api/parvada/{modulo}', timeout=5)
        return jsonify(r.json()), r.status_code
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
        'role': current_user.role,
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
        'role': current_user.role,
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
        'role': current_user.role,
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
        'role': current_user.role,
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
        'role': current_user.role,
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
        'role': current_user.role,
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


