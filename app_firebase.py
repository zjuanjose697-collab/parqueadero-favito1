import os
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo
from flask import Flask, request, redirect, url_for, flash, render_template_string, send_file
from flask_sqlalchemy import SQLAlchemy
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import firebase_admin
from firebase_admin import credentials, firestore
app = Flask(__name__)
app.secret_key = os.environ.get("FAVITO_SECRET_KEY", "cambia-esta-clave-en-produccion")

# Configuración de Zona Horaria para Colombia (UTC-5)
COLOMBIA_TZ = ZoneInfo("America/Bogota")

def hora_colombia():
    # SQLite guarda DateTime sin zona. Usamos hora local de Colombia como datetime naive
    # para evitar errores al restar fechas con y sin timezone.
    return datetime.now(COLOMBIA_TZ).replace(tzinfo=None)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'parqueadero.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- FIREBASE / FIRESTORE ---
FIREBASE_CREDENTIALS = os.environ.get(
    "FIREBASE_CREDENTIALS",
    os.path.join(
        BASE_DIR,
        "parqueadero-favito-firebase-adminsdk-fbsvc-6832080f6a.json"
    )
)

if not os.path.exists(FIREBASE_CREDENTIALS):
    FIREBASE_CREDENTIALS = "/etc/secrets/parqueadero-favito-firebase-adminsdk-fbsvc-6832080f6a.json"

if not firebase_admin._apps:
    firebase_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")

    if firebase_json:
        import json
        cred = credentials.Certificate(json.loads(firebase_json))
    else:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)

    firebase_admin.initialize_app(cred)

firestore_db = firestore.client()

# --- FUNCIONES DE SINCRONIZACION CON FIRESTORE ---
def firestore_guardar(coleccion, documento, datos):
    """Guarda una copia del registro en Firestore sin impedir que FAVITO siga funcionando si la nube falla."""
    try:
        firestore_db.collection(coleccion).document(str(documento)).set(datos)
        print(f"FIRESTORE OK: {coleccion}/{documento}")
        return True
    except Exception as e:
        print(f"FIRESTORE ERROR ({coleccion}/{documento}): {e}")
        return False

def firestore_eliminar(coleccion, documento):
    try:
        firestore_db.collection(coleccion).document(str(documento)).delete()
    except Exception as e:
        print(f"FIRESTORE ERROR eliminando {coleccion}/{documento}: {e}")

db = SQLAlchemy(app)

# --- MODELOS DE BASE DE DATOS ---
class Caja(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    monto_inicial = db.Column(db.Integer, default=0)
    estado = db.Column(db.String(10), default='cerrada')
    fecha_apertura = db.Column(db.DateTime, nullable=True)

class Vehiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(10), nullable=False)
    nombre_cliente = db.Column(db.String(100), nullable=True)
    telefono_cliente = db.Column(db.String(30), nullable=True)
    tipo = db.Column(db.String(20), nullable=False)
    casilla = db.Column(db.Integer, nullable=False)
    fecha_ingreso = db.Column(db.DateTime, default=hora_colombia)
    fecha_salida = db.Column(db.DateTime, nullable=True)
    estado = db.Column(db.String(10), default='activo')
    lavado = db.Column(db.Boolean, default=False)
    costo_lavado = db.Column(db.Integer, default=0)
    costo_servicios = db.Column(db.Integer, default=0)
    metodo_pago = db.Column(db.String(20), default='Efectivo')
    total_pagado = db.Column(db.Integer, default=0)
    preexistente = db.Column(db.Boolean, default=False)

class VentaLavado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(10), nullable=False)
    tipo_vehiculo = db.Column(db.String(50), nullable=False)
    servicio = db.Column(db.String(100), nullable=False)
    monto = db.Column(db.Integer, nullable=False)
    metodo_pago = db.Column(db.String(20), default='Efectivo')
    fecha = db.Column(db.DateTime, default=hora_colombia)

class VentaVulcanizadora(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    servicio = db.Column(db.String(100), nullable=False)
    monto = db.Column(db.Integer, nullable=False)
    metodo_pago = db.Column(db.String(20), default='Efectivo')
    fecha = db.Column(db.DateTime, default=hora_colombia)

class Arriendo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    local = db.Column(db.String(100), nullable=False)
    inquilino = db.Column(db.String(100), nullable=False)
    monto = db.Column(db.Integer, nullable=False)
    metodo_pago = db.Column(db.String(20), default='Efectivo')
    fecha = db.Column(db.DateTime, default=hora_colombia)

class Gasto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200), nullable=False)
    monto = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, default=hora_colombia)

class Tarifa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), unique=True, nullable=False)
    nombre = db.Column(db.String(50), nullable=False)
    tarifa_hora = db.Column(db.Integer, nullable=False)
    tarifa_lavado = db.Column(db.Integer, nullable=False)
    tarifa_mes = db.Column(db.Integer, default=0)

# --- PLANTILLA HTML ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Parqueadero Favito POS Pro</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <style>
        :root {
            --bg-main: #0b1120;
            --bg-card: #151e2e;
            --bg-input: #0f172a;
            --border: #233044;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --green: #10b981;
            --red: #ef4444;
        }
        body { background-color: var(--bg-main); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 20px; }
        .header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; padding-bottom: 15px; border-bottom: 1px solid var(--border); }
        .brand-logo { background: #2563eb; color: white; border-radius: 8px; width: 38px; height: 38px; display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 1.3rem; }
        .stat-card { background-color: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; display: flex; align-items: center; justify-content: space-between; }
        .stat-card p { color: var(--text-muted); font-size: 0.72rem; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; margin-bottom: 4px; }
        .stat-card h2 { font-size: 1.8rem; font-weight: 800; margin: 0; }
        .stat-icon { font-size: 1.5rem; padding: 12px; border-radius: 10px; background: rgba(255, 255, 255, 0.03); display: flex; align-items: center; justify-content: center; }
        .main-nav .nav-link { color: var(--text-muted); border: none; padding: 12px 18px; font-weight: 600; font-size: 0.9rem; border-bottom: 3px solid transparent; border-radius: 0; }
        .main-nav .nav-link:hover { color: var(--text); }
        .main-nav .nav-link.active { color: var(--accent); background: transparent; border-bottom-color: var(--accent); }
        .panel-card { background-color: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 20px; }
        .grid-zonas { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
        @media (max-width: 1200px) { .grid-zonas { grid-template-columns: repeat(3, 1fr); } }
        @media (max-width: 768px) { .grid-zonas { grid-template-columns: repeat(1, 1fr); } }
        .zona-box { background-color: #0e1622; border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
        .zona-title { color: var(--accent); font-weight: bold; font-size: 0.82rem; margin-bottom: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .celda-slot { border-radius: 6px; padding: 8px 2px; text-align: center; font-weight: bold; font-size: 0.8rem; }
        .celda-libre { background-color: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.3); color: var(--green); }
        .celda-ocupada { background-color: rgba(239, 68, 68, 0.15); border: 1px solid var(--red); color: var(--red); }
        .form-control, .form-select { background-color: var(--bg-input); border: 1px solid var(--border); color: var(--text); padding: 10px 14px; border-radius: 8px; }
        .form-control:focus, .form-select:focus { background-color: var(--bg-input); border-color: var(--accent); color: var(--text); box-shadow: none; }
        .form-label { color: var(--text-muted); font-size: 0.78rem; font-weight: 700; text-transform: uppercase; }
        .table-custom { color: var(--text); }
        .table-custom th { color: var(--text-muted); font-size: 0.75rem; text-transform: uppercase; border-bottom: 1px solid var(--border); padding: 12px; }
        .table-custom td { border-bottom: 1px solid var(--border); padding: 12px; vertical-align: middle; }
    </style>
</head>
<body>

    <div class="header-bar">
        <div class="d-flex align-items-center gap-3">
            <div class="brand-logo">P</div>
            <div>
                <h4 class="m-0 fw-bold">Parqueadero Favito</h4>
                <small class="text-muted">POS Pro, Lavadero & Vulcanizadora</small>
            </div>
        </div>
        <div class="d-flex align-items-center gap-2">
            {% if caja and caja.estado == 'abierta' %}
                <span class="badge bg-success bg-opacity-20 text-success border border-success px-3 py-2">Caja Abierta (Base: ${{ "{:,}".format(caja.monto_inicial).replace(',', '.') }})</span>
                <form action="/caja/cerrar" method="POST" class="d-inline">
                    <button type="submit" class="btn btn-danger fw-bold px-3 py-2">Cerrar Caja</button>
                </form>
            {% else %}
                <span class="badge bg-danger bg-opacity-20 text-danger border border-danger px-3 py-2">Caja Cerrada</span>
                <button type="button" class="btn btn-success fw-bold px-3 py-2" data-bs-toggle="modal" data-bs-target="#modalAbrirCaja">Abrir Caja</button>
            {% endif %}
        </div>
    </div>

    <div class="row g-3 mb-4">
        {% if caja and caja.estado == 'abierta' %}
        <div class="col-md-3">
            <div class="stat-card">
                <div><p>VEHÍCULOS ENTRADOS HOY</p><h2>{{ entrados_hoy }}</h2></div>
                <div class="stat-icon text-primary"><i class="bi bi-car-front-fill"></i></div>
            </div>
        </div>
        {% endif %}
        <div class="col-md-3">
            <div class="stat-card">
                <div><p>OCUPACIÓN CELDAS</p><h2>{{ total_ocupados }} <span style="font-size: 1.1rem; color: var(--text-muted);">/ 60</span></h2></div>
                <div class="stat-icon text-warning"><i class="bi bi-shop"></i></div>
            </div>
        </div>
        {% if caja and caja.estado == 'abierta' %}
        <div class="col-md-3">
            <div class="stat-card">
                <div><p>VENTAS TURNO ACTIVO</p><h2 style="color: var(--green);">${{ "{:,}".format(total_ventas).replace(',', '.') }}</h2></div>
                <div class="stat-icon text-success"><i class="bi bi-cash-register"></i></div>
            </div>
        </div>
        {% endif %}
        <div class="col-md-3">
            <div class="stat-card">
                <div><p>GASTOS TURNO ACTIVO</p><h2 style="color: var(--red);">${{ "{:,}".format(total_gastos).replace(',', '.') }}</h2></div>
                <div class="stat-icon text-danger"><i class="bi bi-receipt-cutoff"></i></div>
            </div>
        </div>
    </div>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="alert alert-{{ 'danger' if category == 'error' else 'success' }} alert-dismissible fade show" role="alert">
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                </div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <ul class="nav main-nav mb-4 border-bottom border-secondary-subtle">
        <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#tab-mapa"><i class="bi bi-map me-1"></i> Mapa 6 Zonas</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-entradas"><i class="bi bi-box-arrow-in-right me-1"></i> Entradas y Cobros POS</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-vulcanizadora"><i class="bi bi-wrench me-1"></i> Vulcanizadora</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-lavado"><i class="bi bi-droplet-half me-1"></i> Lavado</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-arriendos"><i class="bi bi-house me-1"></i> Arriendos & Locales</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-gastos"><i class="bi bi-wallet2 me-1"></i> Control Gastos</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-auditoria"><i class="bi bi-pie-chart me-1"></i> Auditoría Contable</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-ajustes"><i class="bi bi-sliders me-1"></i> Ajuste de Precios / Mes</button></li>
    </ul>

    <div class="tab-content">
        
        <div class="tab-pane fade show active" id="tab-mapa">
            <div class="panel-card">
                <h5 class="fw-bold mb-3"><i class="bi bi-grid-3x3-gap-fill text-info me-2"></i> Estado de Secciones</h5>
                <div class="grid-zonas">
                    {% set zonas = [
                        ('Zona 1: Motos', '🛵', range(1, 11)),
                        ('Zona 2: Carros', '🚗', range(11, 21)),
                        ('Zona 3: Volquetas', '🚛', range(21, 31)),
                        ('Zona 4: Especial A', '🚙', range(31, 41)),
                        ('Zona 5: Especial B', '🚚', range(41, 51)),
                        ('Zona 6: Carros Grandes', '🚌', range(51, 61))
                    ] %}
                    {% for nombre_zona, icono, rango in zonas %}
                    <div class="zona-box">
                        <div class="zona-title">{{ icono }} {{ nombre_zona }}</div>
                        <div class="row row-cols-2 g-2">
                            {% for n in rango %}
                                {% set veh = casillas.get(n) %}
                                <div class="col">
                                    {% if veh %}
                                        <div class="celda-slot celda-ocupada">{{ veh.placa }}</div>
                                    {% else %}
                                        <div class="celda-slot celda-libre">E-{{ n }}</div>
                                    {% endif %}
                                </div>
                            {% endfor %}
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div class="tab-pane fade" id="tab-entradas">
            <div class="row">
                <div class="col-md-4">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3"><i class="bi bi-plus-circle text-info me-2"></i> Registrar Entrada</h5>
                        <div class="alert alert-info py-2 small">
                                <i class="bi bi-calendar3 me-1"></i>
                                Puedes registrar la fecha/hora manualmente para vehículos antiguos.
                            </div>
                            <form action="/ingresar" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Placa del Vehículo</label>
                                <input type="text" name="placa" class="form-control text-uppercase font-monospace fs-5 fw-bold" placeholder="EJ: ABC123" required autocomplete="off">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Nombre del Cliente</label>
                                <input type="text" name="nombre_cliente" class="form-control" placeholder="Nombre completo" maxlength="100" required autocomplete="name">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Número de Teléfono</label>
                                <input type="tel" name="telefono_cliente" class="form-control" placeholder="Ej: 3001234567" maxlength="30" required autocomplete="tel">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Tipo de Vehículo</label>
                                <select name="tipo" class="form-select" required>
                                    {% for t in tarifas %}
                                        <option value="{{ t.tipo }}">{{ t.nombre }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Número de Casilla (1 - 60)</label>
                                <input type="number" name="casilla" min="1" max="60" class="form-control" placeholder="Número E-1 a E-60" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Fecha y hora de ingreso</label>
                                <input type="datetime-local" name="fecha_ingreso" class="form-control">
                                <small class="text-muted">Si la dejas vacía, se usa la fecha/hora actual de Colombia.</small>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Servicios Extra / Insumos ($)</label>
                                <input type="text" name="costo_servicios" class="form-control" value="0" required>
                            </div>
                            <button type="submit" class="btn btn-info w-100 fw-bold py-2"><i class="bi bi-save me-1"></i> Ingresar Vehículo</button>
                        </form>
                    </div>
                </div>

                <div class="col-md-8">
                    <div class="panel-card">
                        
                        <div class="panel-card">
                            <h5 class="fw-bold mb-3">
                                <i class="bi bi-clock-history text-warning me-2"></i>Registrar Vehículo Preexistente
                            </h5>
                            <p class="text-muted small">
                                Use esta opción para registrar vehículos que ya estaban dentro del parqueadero antes de comenzar a usar Favito POS.
                            </p>
                            <form action="/ingresar_preexistente" method="POST">
                                <div class="row g-3">
                                    <div class="col-md-3">
                                        <label class="form-label">Placa</label>
                                        <input type="text" name="placa" class="form-control text-uppercase" maxlength="10" required>
                                    </div>
                                    <div class="col-md-3">
                                        <label class="form-label">Tipo</label>
                                        <select name="tipo" class="form-select" required>
                                            <option value="moto">Moto</option>
                                            <option value="carro">Carro</option>
                                            <option value="volqueta">Volqueta</option>
                                            <option value="especial">Especial</option>
                                        </select>
                                    </div>
                                    <div class="col-md-3">
                                        <label class="form-label">Casilla</label>
                                        <input type="number" name="casilla" class="form-control" min="1" max="60" required>
                                    </div>
                                    <div class="col-md-3">
                                        <label class="form-label">Fecha/hora de ingreso</label>
                                        <input type="datetime-local" name="fecha_ingreso" class="form-control">
                                    </div>
                                </div>
                                <button class="btn btn-warning mt-3 fw-bold">
                                    <i class="bi bi-plus-circle me-1"></i> Registrar como ya existente
                                </button>
                            </form>
                        </div>

<h5 class="fw-bold mb-3"><i class="bi bi-car-front text-success me-2"></i> Vehículos Adentro ({{ activos|length }})</h5>
                        {% if activos %}
                            <div class="table-responsive">
                                <table class="table table-custom align-middle">
                                    <thead>
                                        <tr>
                                            <th>Placa</th>
                                            <th>Nombre</th>
                                            <th>Teléfono</th>
                                            <th>Tipo</th>
                                            <th>Casilla</th>
                                            <th>Ingreso</th>
                                            <th>Lavado</th>
                                            <th>Factura</th>
                                            <th>Cobro y Salida</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for v in activos %}
                                            <tr>
                                                <td class="fw-bold font-monospace fs-5 text-info">{{ v.placa }}</td>
                                                <td class="fw-semibold">{{ v.nombre_cliente or '—' }}</td>
                                                <td>{{ v.telefono_cliente or '—' }}</td>
                                                <td>{{ v.tipo }}</td>
                                                <td><span class="badge bg-secondary">Casilla E-{{ v.casilla }}</span></td>
                                                <td>{{ v.fecha_ingreso.strftime('%H:%M - %d/%m') }}</td>
                                                <td>{{ "Sí ($" ~ "{:,}".format(v.costo_lavado).replace(',', '.') ~ ")" if v.lavado else "No" }}</td>
                                                <td>
                                                    <a href="{{ url_for('comprobante_ingreso', id=v.id) }}" class="btn btn-outline-primary btn-sm fw-bold">
                                                        <i class="bi bi-receipt me-1"></i> Factura
                                                    </a>
                                                </td>
                                                <td>
                                                    <form action="/salida/{{ v.id }}" method="POST" class="d-flex gap-2 align-items-center">
                                                        <select name="tipo_cobro" class="form-select form-select-sm" style="width: 110px;">
                                                            <option value="hora">Por Hora</option>
                                                            <option value="mes">Mensualidad</option>
                                                        </select>
                                                        <select name="metodo_pago" class="form-select form-select-sm" style="width: 105px;">
                                                            <option value="Efectivo">Efectivo</option>
                                                            <option value="Transferencia">Transf.</option>
                                                        </select>
                                                        <button type="submit" class="btn btn-danger btn-sm fw-bold px-2">Cobrar</button>
                                                    </form>
                                                </td>
                                            </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        {% else %}
                            <p class="text-muted text-center my-4">No hay vehículos parqueados actualmente.</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>


        <div class="tab-pane fade" id="tab-lavado">
            <div class="row">
                <div class="col-md-4">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3 text-info"><i class="bi bi-droplet-half me-2"></i> Registrar Lavado</h5>
                        <p class="text-muted small">El lavado se registra por separado del ingreso del vehículo y queda incluido en el cierre de caja.</p>
                        <form action="/lavado/venta" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Placa</label>
                                <input type="text" name="placa" class="form-control text-uppercase" maxlength="10" placeholder="Ej: ABC123" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Tipo de Vehículo</label>
                                <select name="tipo_vehiculo" class="form-select" required>
                                    {% for t in tarifas %}
                                        <option value="{{ t.tipo }}">{{ t.nombre }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Servicio</label>
                                <select name="servicio" class="form-select" required>
                                    <option value="Lavado básico">Lavado básico</option>
                                    <option value="Lavado completo">Lavado completo</option>
                                    <option value="Lavado + aspirado">Lavado + aspirado</option>
                                    <option value="Otro">Otro</option>
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Fecha y hora</label>
                                <input type="datetime-local" name="fecha" class="form-control">
                                <small class="text-muted">Vacía = fecha/hora actual de Colombia.</small>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Cobro Total ($)</label>
                                <input type="text" name="monto" class="form-control" placeholder="Ej: 10.000" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Método de Pago</label>
                                <select name="metodo_pago" class="form-select">
                                    <option value="Efectivo">Efectivo</option>
                                    <option value="Transferencia">Transferencia</option>
                                </select>
                            </div>
                            <button type="submit" class="btn btn-info w-100 fw-bold">
                                <i class="bi bi-check-circle me-1"></i> Registrar Lavado
                            </button>
                        </form>
                    </div>
                </div>
                <div class="col-md-8">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3"><i class="bi bi-journal-text me-2"></i> Historial de Lavados</h5>
                        <div class="table-responsive">
                            <table class="table table-custom">
                                <thead>
                                    <tr>
                                        <th>Fecha / Hora</th>
                                        <th>Placa</th>
                                        <th>Tipo</th>
                                        <th>Servicio</th>
                                        <th>Método</th>
                                        <th>Total</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for v in ventas_lavado %}
                                        <tr>
                                            <td>{{ v.fecha.strftime('%H:%M - %d/%m/%Y') }}</td>
                                            <td class="fw-bold text-info">{{ v.placa }}</td>
                                            <td>{{ v.tipo_vehiculo }}</td>
                                            <td>{{ v.servicio }}</td>
                                            <td><span class="badge bg-secondary">{{ v.metodo_pago }}</span></td>
                                            <td class="text-success fw-bold">${{ "{:,}".format(v.monto).replace(',', '.') }}</td>
                                        </tr>
                                    {% else %}
                                        <tr><td colspan="6" class="text-center text-muted py-4">No hay lavados registrados.</td></tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="tab-pane fade" id="tab-vulcanizadora">
            <div class="row">
                <div class="col-md-4">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3 text-warning"><i class="bi bi-wrench me-2"></i> Venta de Vulcanizadora</h5>
                        <form action="/vulcanizadora/venta" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Categoría / Tipo de Producto</label>
                                <select name="categoria" class="form-select mb-2" id="selectCategoria" onchange="actualizarOpcionesVulcanizadora(this)">
                                    <option value="Parche">Parche (Ref)</option>
                                    <option value="Neumatico">Neumático (Medida)</option>
                                    <option value="Valvula">Válvula</option>
                                    <option value="Otro">Otro Servicio Personalizado</option>
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Detalle Específico</label>
                                <select name="detalle_select" class="form-select" id="selectDetalle"></select>
                                <input type="text" name="detalle_custom" id="inputCustom" class="form-control d-none mt-2" placeholder="Especificar servicio...">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Cobro Total ($)</label>
                                <input type="text" name="monto" class="form-control" placeholder="Ej: 15.000" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Método de Pago</label>
                                <select name="metodo_pago" class="form-select">
                                    <option value="Efectivo">Efectivo</option>
                                    <option value="Transferencia">Transferencia</option>
                                </select>
                            </div>
                            <button type="submit" class="btn btn-warning w-100 fw-bold text-dark"><i class="bi bi-check-circle me-1"></i> Registrar Venta</button>
                        </form>
                    </div>
                </div>
                <div class="col-md-8">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3"><i class="bi bi-journal-text me-2"></i> Historial de Vulcanizadora</h5>
                        <table class="table table-custom">
                            <thead>
                                <tr>
                                    <th>Fecha / Hora</th>
                                    <th>Producto / Servicio</th>
                                    <th>Método</th>
                                    <th>Total Pagado</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for v in ventas_vulcanizadora %}
                                    <tr>
                                        <td>{{ v.fecha.strftime('%H:%M - %d/%m/%Y') }}</td>
                                        <td class="fw-bold text-info">{{ v.servicio }}</td>
                                        <td><span class="badge bg-secondary">{{ v.metodo_pago }}</span></td>
                                        <td class="text-success fw-bold">${{ "{:,}".format(v.monto).replace(',', '.') }}</td>
                                    </tr>
                                {% else %}
                                    <tr><td colspan="4" class="text-center text-muted py-4">No hay ventas registradas en vulcanizadora.</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div class="tab-pane fade" id="tab-arriendos">
            <div class="row">
                <div class="col-md-4">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3"><i class="bi bi-house text-info me-2"></i> Recibo de Arriendo</h5>
                        <form action="/arriendos/pagar" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Local / Espacio</label>
                                <input type="text" name="local" class="form-control" placeholder="Ej: Local 1, Lavadero" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Nombre del Inquilino</label>
                                <input type="text" name="inquilino" class="form-control" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Valor Recibido ($)</label>
                                <input type="text" name="monto" class="form-control" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Método de Pago</label>
                                <select name="metodo_pago" class="form-select">
                                    <option value="Efectivo">Efectivo</option>
                                    <option value="Transferencia">Transferencia</option>
                                </select>
                            </div>
                            <button type="submit" class="btn btn-info w-100 fw-bold">Guardar Pago</button>
                        </form>
                    </div>
                </div>
                <div class="col-md-8">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3"><i class="bi bi-journal-check me-2"></i> Historial de Arriendos</h5>
                        <table class="table table-custom">
                            <thead>
                                <tr>
                                    <th>Fecha</th>
                                    <th>Local</th>
                                    <th>Inquilino</th>
                                    <th>Método</th>
                                    <th>Valor</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for a in lista_arriendos %}
                                    <tr>
                                        <td>{{ a.fecha.strftime('%d/%m/%Y %H:%M') }}</td>
                                        <td>{{ a.local }}</td>
                                        <td>{{ a.inquilino }}</td>
                                        <td><span class="badge bg-secondary">{{ a.metodo_pago }}</span></td>
                                        <td class="text-success fw-bold">${{ "{:,}".format(a.monto).replace(',', '.') }}</td>
                                    </tr>
                                {% else %}
                                    <tr><td colspan="5" class="text-center text-muted py-4">No hay pagos de arriendos registrados.</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div class="tab-pane fade" id="tab-gastos">
            <div class="row">
                <div class="col-md-4">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3"><i class="bi bi-dash-circle text-danger me-2"></i> Registrar Gasto</h5>
                        <form action="/gasto" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Descripción del Gasto</label>
                                <input type="text" name="descripcion" class="form-control" placeholder="Ej: Compra de insumos" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Monto ($)</label>
                                <input type="text" name="monto" class="form-control" required>
                            </div>
                            <button type="submit" class="btn btn-danger w-100 fw-bold">Guardar Gasto</button>
                        </form>
                    </div>
                </div>
                <div class="col-md-8">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3"><i class="bi bi-receipt me-2"></i> Historial de Gastos</h5>
                        <table class="table table-custom">
                            <thead>
                                <tr>
                                    <th>Hora</th>
                                    <th>Descripción</th>
                                    <th>Monto</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for g in lista_gastos %}
                                    <tr>
                                        <td>{{ g.fecha.strftime('%H:%M - %d/%m') }}</td>
                                        <td>{{ g.descripcion }}</td>
                                        <td class="text-danger fw-bold">${{ "{:,}".format(g.monto).replace(',', '.') }}</td>
                                    </tr>
                                {% else %}
                                    <tr><td colspan="3" class="text-center text-muted py-4">Sin gastos en el turno.</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div class="tab-pane fade" id="tab-auditoria">
            <div class="panel-card">
                <h5 class="fw-bold mb-4 text-info"><i class="bi bi-pie-chart me-2"></i> Balance del Turno Actual</h5>
                <div class="row g-4 text-center">
                    <div class="col-md-3">
                        <div class="p-3 border border-light rounded bg-light text-dark">
                            <small class="text-secondary d-block font-uppercase fw-bold">Base Inicial</small>
                            <span class="fs-4 fw-bold">${{ "{:,}".format(caja.monto_inicial).replace(',', '.') if caja else 0 }}</span>
                        </div>
                    </div>
                    <div class="col-md-3">
                        <div class="p-3 border border-success rounded bg-success bg-opacity-10 text-success">
                            <small class="d-block font-uppercase fw-bold">Ingresos Efectivo</small>
                            <span class="fs-4 fw-bold">${{ "{:,}".format(total_efectivo).replace(',', '.') }}</span>
                        </div>
                    </div>
                    <div class="col-md-3">
                        <div class="p-3 border border-info rounded bg-info bg-opacity-10 text-info">
                            <small class="d-block font-uppercase fw-bold">Ingresos Transferencia</small>
                            <span class="fs-4 fw-bold">${{ "{:,}".format(total_transferencia).replace(',', '.') }}</span>
                        </div>
                    </div>
                    <div class="col-md-3">
                        <div class="p-3 border border-danger rounded bg-danger bg-opacity-10 text-danger">
                            <small class="d-block font-uppercase fw-bold">Total Gastos</small>
                            <span class="fs-4 fw-bold">${{ "{:,}".format(total_gastos).replace(',', '.') }}</span>
                        </div>
                    </div>
                </div>
                <hr class="my-4 border-secondary">
                <div class="d-flex justify-content-between align-items-center bg-dark p-3 rounded border border-warning">
                    <h4 class="text-light m-0">Total Neto Sugerido en Caja (Base + Efectivo - Gastos):</h4>
                    <h3 class="text-warning fw-bold m-0">${{ "{:,}".format((caja.monto_inicial if caja else 0) + total_efectivo - total_gastos).replace(',', '.') }}</h3>
                </div>
            </div>
        </div>

        <div class="tab-pane fade" id="tab-ajustes">
            <div class="panel-card">
                <h5 class="fw-bold mb-3"><i class="bi bi-sliders text-success me-2"></i> Configuración de Tarifas (Hora y Mensualidad)</h5>
                <div class="table-responsive">
                    <table class="table table-custom">
                        <thead>
                            <tr>
                                <th>Tipo de Vehículo</th>
                                <th>Tarifa por Hora</th>
                                <th>Tarifa Lavado</th>
                                <th>Mensualidad</th>
                                <th>Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for t in tarifas %}
                            <tr>
                                <form action="/tarifas/actualizar/{{ t.id }}" method="POST">
                                    <td class="fw-bold text-info">{{ t.nombre }}</td>
                                    <td><input type="text" name="tarifa_hora" class="form-control form-control-sm" value="{{ t.tarifa_hora }}"></td>
                                    <td><input type="text" name="tarifa_lavado" class="form-control form-control-sm" value="{{ t.tarifa_lavado }}"></td>
                                    <td><input type="text" name="tarifa_mes" class="form-control form-control-sm" value="{{ t.tarifa_mes }}"></td>
                                    <td><button type="submit" class="btn btn-success btn-sm fw-bold">Guardar</button></td>
                                </form>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

    </div>

    <!-- Modal Abrir Caja -->
    <div class="modal fade" id="modalAbrirCaja" tabindex="-1">
        <div class="modal-dialog">
            <div class="modal-content bg-dark text-light border border-secondary">
                <form action="/caja/abrir" method="POST">
                    <div class="modal-header border-secondary">
                        <h5 class="modal-title fw-bold">Apertura de Caja</h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="mb-3">
                            <label class="form-label">Monto Inicial / Base en Efectivo ($)</label>
                            <input type="text" name="monto_inicial" class="form-control" value="50.000" required>
                        </div>
                    </div>
                    <div class="modal-footer border-secondary">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
                        <button type="submit" class="btn btn-success fw-bold">Abrir Turno</button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const opcionesVulcanizadora = {
            'Parche': ['Parche Sencillo Moto', 'Parche Radial Carro', 'Parche Especial Camión'],
            'Neumatico': ['Neumático R13 / R14', 'Neumático R15 / R16', 'Neumático Moto Delantero/Trasero'],
            'Valvula': ['Válvula Tradicional Carro', 'Válvula de Lujo / Camión'],
            'Otro': []
        };

        function actualizarOpcionesVulcanizadora(selectElem) {
            const categoria = selectElem.value;
            const selectDetalle = document.getElementById('selectDetalle');
            const inputCustom = document.getElementById('inputCustom');
            selectDetalle.innerHTML = '';
            
            if (categoria === 'Otro') {
                selectDetalle.classList.add('d-none');
                inputCustom.classList.remove('d-none');
                inputCustom.required = true;
            } else {
                selectDetalle.classList.remove('d-none');
                inputCustom.classList.add('d-none');
                inputCustom.required = false;
                
                (opcionesVulcanizadora[categoria] || []).forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item;
                    opt.textContent = item;
                    selectDetalle.appendChild(opt);
                });
            }
        }
        document.addEventListener('DOMContentLoaded', () => {
            const catSelect = document.getElementById('selectCategoria');
            if(catSelect) actualizarOpcionesVulcanizadora(catSelect);
        });
    </script>
</body>
</html>
"""

# --- RUTAS Y CONTROLADORES ---

def limpiar_monto(valor_str):
    if not valor_str:
        return 0
    limpio = str(valor_str).replace('.', '').replace('$', '').replace(',', '').strip()
    try:
        return int(limpio)
    except ValueError:
        return 0

@app.route('/')
def index():
    caja = Caja.query.first()
    if not caja:
        caja = Caja(monto_inicial=0, estado='cerrada')
        db.session.add(caja)
        db.session.commit()

    tarifas = Tarifa.query.all()
    if not tarifas:
        defaults = [
            ('moto', 'Moto', 2000, 5000, 40000),
            ('carro', 'Carro', 4000, 10000, 90000),
            ('volqueta', 'Volqueta / Camión', 7000, 18000, 180000),
            ('especial_a', 'Especial A', 5000, 12000, 120000),
            ('especial_b', 'Especial B', 6000, 15000, 150000),
            ('grande', 'Carro Grande / Bus', 8000, 20000, 200000),
        ]
        for t_tipo, t_nombre, t_h, t_l, t_m in defaults:
            db.session.add(Tarifa(tipo=t_tipo, nombre=t_nombre, tarifa_hora=t_h, tarifa_lavado=t_l, tarifa_mes=t_m))
        db.session.commit()
        tarifas = Tarifa.query.all()

    activos = Vehiculo.query.filter_by(estado='activo').order_by(Vehiculo.casilla).all()
    casillas = {v.casilla: v for v in activos}

    ahora = hora_colombia()
    hoy_inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    entrados_hoy = Vehiculo.query.filter(Vehiculo.fecha_ingreso >= hoy_inicio).count()

    # Totales del turno activo. Si la caja está cerrada, mostramos los totales del día.
    desde = caja.fecha_apertura if caja.estado == 'abierta' and caja.fecha_apertura else hoy_inicio

    vehiculos_cobrados = Vehiculo.query.filter(
        Vehiculo.estado == 'salida', Vehiculo.fecha_salida >= desde
    ).all()
    ventas_vulc = VentaVulcanizadora.query.filter(VentaVulcanizadora.fecha >= desde).all()
    ventas_lavado = VentaLavado.query.filter(VentaLavado.fecha >= desde).all()
    lista_arriendos = Arriendo.query.filter(Arriendo.fecha >= desde).all()
    lista_gastos = Gasto.query.filter(Gasto.fecha >= desde).all()

    total_ventas = (
        sum(v.total_pagado for v in vehiculos_cobrados)
        + sum(v.monto for v in ventas_vulc)
        + sum(v.monto for v in ventas_lavado)
        + sum(a.monto for a in lista_arriendos)
    )
    total_gastos = sum(g.monto for g in lista_gastos)

    total_efectivo = (
        sum(v.total_pagado for v in vehiculos_cobrados if v.metodo_pago == 'Efectivo')
        + sum(v.monto for v in ventas_vulc if v.metodo_pago == 'Efectivo')
        + sum(v.monto for v in ventas_lavado if v.metodo_pago == 'Efectivo')
        + sum(a.monto for a in lista_arriendos if a.metodo_pago == 'Efectivo')
    )
    total_transferencia = (
        sum(v.total_pagado for v in vehiculos_cobrados if v.metodo_pago == 'Transferencia')
        + sum(v.monto for v in ventas_vulc if v.metodo_pago == 'Transferencia')
        + sum(v.monto for v in ventas_lavado if v.metodo_pago == 'Transferencia')
        + sum(a.monto for a in lista_arriendos if a.metodo_pago == 'Transferencia')
    )

    return render_template_string(
        HTML_TEMPLATE,
        caja=caja,
        tarifas=tarifas,
        activos=activos,
        casillas=casillas,
        entrados_hoy=entrados_hoy,
        total_ocupados=len(activos),
        total_ventas=total_ventas,
        total_gastos=total_gastos,
        total_efectivo=total_efectivo,
        total_transferencia=total_transferencia,
        ventas_vulcanizadora=ventas_vulc,
        ventas_lavado=ventas_lavado,
        lista_arriendos=lista_arriendos,
        lista_gastos=lista_gastos,
    )

@app.route('/caja/abrir', methods=['POST'])
def abrir_caja():
    caja = Caja.query.first()
    if caja and caja.estado == 'abierta':
        flash("La caja ya está abierta.", "error")
        return redirect(url_for('index'))

    monto = limpiar_monto(request.form.get('monto_inicial'))
    if monto < 0:
        flash("El monto inicial no puede ser negativo.", "error")
        return redirect(url_for('index'))

    caja.monto_inicial = monto
    caja.estado = 'abierta'
    caja.fecha_apertura = hora_colombia()
    db.session.commit()
    flash(f"Caja abierta correctamente con base de ${monto:,}.".replace(',', '.'), "success")
    return redirect(url_for('index'))


@app.route('/caja/nuevo_dia', methods=['POST'])
def nuevo_dia():
    """Reinicia las casillas de vehículos que ya salieron, conservando el historial."""
    ahora = hora_colombia()

    # Los vehículos con estado 'salida' ya no ocupan casilla.
    # Los vehículos 'activo' permanecen dentro del parqueadero.
    liberados = Vehiculo.query.filter_by(estado='salida').count()

    # Abrimos una nueva caja para el nuevo día.
    caja = Caja.query.first()
    if caja:
        caja.estado = 'abierta'
        caja.fecha_apertura = ahora
        caja.monto_inicial = limpiar_monto(request.form.get('monto_inicial', 0))
    else:
        caja = Caja(
            estado='abierta',
            fecha_apertura=ahora,
            monto_inicial=limpiar_monto(request.form.get('monto_inicial', 0))
        )
        db.session.add(caja)

    db.session.commit()

    flash(
        f"Nuevo día iniciado. {liberados} registros anteriores quedan en el historial. "
        "Los vehículos que continúan dentro siguen ocupando sus casillas.",
        "success"
    )
    return redirect(url_for('index'))


@app.route('/caja/cerrar', methods=['POST'])
def cerrar_caja():
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("La caja ya está cerrada.", "error")
        return redirect(url_for('index'))

    ahora = hora_colombia()
    desde = caja.fecha_apertura or ahora.replace(hour=0, minute=0, second=0, microsecond=0)

    vehiculos = Vehiculo.query.filter(
        Vehiculo.estado == 'salida',
        Vehiculo.fecha_salida >= desde,
        Vehiculo.fecha_salida <= ahora
    ).all()

    vulc = VentaVulcanizadora.query.filter(
        VentaVulcanizadora.fecha >= desde,
        VentaVulcanizadora.fecha <= ahora
    ).all()

    lavados = VentaLavado.query.filter(
        VentaLavado.fecha >= desde,
        VentaLavado.fecha <= ahora
    ).all()

    arriendos = Arriendo.query.filter(
        Arriendo.fecha >= desde,
        Arriendo.fecha <= ahora
    ).all()

    gastos = Gasto.query.filter(
        Gasto.fecha >= desde,
        Gasto.fecha <= ahora
    ).all()

    efectivo_parqueadero = sum(v.total_pagado for v in vehiculos if v.metodo_pago == 'Efectivo')
    transferencia_parqueadero = sum(v.total_pagado for v in vehiculos if v.metodo_pago == 'Transferencia')
    efectivo_vulc = sum(v.monto for v in vulc if v.metodo_pago == 'Efectivo')
    transferencia_vulc = sum(v.monto for v in vulc if v.metodo_pago == 'Transferencia')
    efectivo_lavados = sum(v.monto for v in lavados if v.metodo_pago == 'Efectivo')
    transferencia_lavados = sum(v.monto for v in lavados if v.metodo_pago == 'Transferencia')
    efectivo_arriendos = sum(a.monto for a in arriendos if a.metodo_pago == 'Efectivo')
    transferencia_arriendos = sum(a.monto for a in arriendos if a.metodo_pago == 'Transferencia')

    efectivo = efectivo_parqueadero + efectivo_vulc + efectivo_lavados + efectivo_arriendos
    transferencia = transferencia_parqueadero + transferencia_vulc + transferencia_lavados + transferencia_arriendos
    total_ingresos = efectivo + transferencia
    total_gastos = sum(g.monto for g in gastos)
    efectivo_esperado = caja.monto_inicial + efectivo - total_gastos

    def dinero(valor):
        return "${:,.0f}".format(valor or 0).replace(",", ".")

    pdf = BytesIO()
    c = canvas.Canvas(pdf, pagesize=letter)
    _, alto = letter
    y = alto - 45

    def escribir(texto, salto=18, negrita=False, tam=10):
        nonlocal y
        if y < 65:
            c.showPage()
            y = alto - 45
        c.setFont("Helvetica-Bold" if negrita else "Helvetica", tam)
        c.drawString(45, y, str(texto))
        y -= salto

    c.setFont("Helvetica-Bold", 20)
    c.drawString(45, y, "FAVITO POS")
    y -= 25
    c.setFont("Helvetica-Bold", 14)
    c.drawString(45, y, "REPORTE DE CIERRE DE CAJA")
    y -= 28

    escribir("Fecha de apertura: " + desde.strftime("%d/%m/%Y %H:%M"))
    escribir("Fecha de cierre:   " + ahora.strftime("%d/%m/%Y %H:%M"))
    y -= 8

    escribir("RESUMEN FINANCIERO", 20, True, 12)
    escribir("Base inicial: " + dinero(caja.monto_inicial))
    escribir("Parqueadero - efectivo: " + dinero(efectivo_parqueadero))
    escribir("Parqueadero - transferencia: " + dinero(transferencia_parqueadero))
    escribir("Vulcanizadora - efectivo: " + dinero(efectivo_vulc))
    escribir("Vulcanizadora - transferencia: " + dinero(transferencia_vulc))
    escribir("Lavado - efectivo: " + dinero(efectivo_lavados))
    escribir("Lavado - transferencia: " + dinero(transferencia_lavados))
    escribir("Arriendos - efectivo: " + dinero(efectivo_arriendos))
    escribir("Arriendos - transferencia: " + dinero(transferencia_arriendos))
    escribir("TOTAL INGRESOS: " + dinero(total_ingresos), 20, True)
    escribir("TOTAL GASTOS: " + dinero(total_gastos), 20, True)
    escribir("EFECTIVO ESPERADO EN CAJA: " + dinero(efectivo_esperado), 24, True, 12)

    escribir("OPERACIONES DEL TURNO", 20, True, 12)
    escribir("Vehículos cobrados: " + str(len(vehiculos)))
    escribir("Servicios de vulcanizadora: " + str(len(vulc)))
    escribir("Servicios de lavado: " + str(len(lavados)))
    escribir("Pagos de arriendo: " + str(len(arriendos)))
    escribir("Gastos registrados: " + str(len(gastos)))

    escribir("DETALLE DE INGRESOS", 20, True, 12)
    for v in vehiculos:
        escribir(f"Parqueadero | {v.placa} | {v.metodo_pago} | {dinero(v.total_pagado)}", 15, False, 9)
    for v in vulc:
        escribir(f"Vulcanizadora | {v.servicio} | {v.metodo_pago} | {dinero(v.monto)}", 15, False, 9)
    for v in lavados:
        escribir(f"Lavado | {v.placa} | {v.servicio} | {v.metodo_pago} | {dinero(v.monto)}", 15, False, 9)
    for a in arriendos:
        escribir(f"Arriendo | {a.local} | {a.inquilino} | {a.metodo_pago} | {dinero(a.monto)}", 15, False, 9)

    escribir("DETALLE DE GASTOS", 20, True, 12)
    for g in gastos:
        escribir(f"{g.descripcion} | {dinero(g.monto)}", 15, False, 9)

    escribir("CIERRE FINAL", 20, True, 12)
    escribir("Base inicial + efectivo - gastos: " + dinero(efectivo_esperado), 20, True)
    escribir("Documento generado automáticamente por Favito POS.", 18, False, 9)

    c.save()
    pdf.seek(0)

    caja.estado = 'cerrada'
    db.session.commit()

    return send_file(
        pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"cierre_caja_{ahora.strftime('%Y%m%d_%H%M%S')}.pdf"
    )



@app.route('/ingresar_preexistente', methods=['POST'])
def ingresar_preexistente():
    placa = (request.form.get('placa') or '').strip().upper()
    tipo = (request.form.get('tipo') or 'carro').strip().lower()

    try:
        casilla = int(request.form.get('casilla'))
    except (TypeError, ValueError):
        casilla = 0

    fecha_raw = (request.form.get('fecha_ingreso') or '').strip()

    if not placa:
        flash("Debes ingresar la placa.", "error")
        return redirect(url_for('index'))

    if casilla < 1 or casilla > 60:
        flash("La casilla debe estar entre 1 y 60.", "error")
        return redirect(url_for('index'))

    existente_placa = Vehiculo.query.filter_by(placa=placa, estado='activo').first()
    if existente_placa:
        flash(f"El vehículo {placa} ya aparece dentro del parqueadero.", "error")
        return redirect(url_for('index'))

    existente_casilla = Vehiculo.query.filter_by(casilla=casilla, estado='activo').first()
    if existente_casilla:
        flash(
            f"La casilla E-{casilla} ya está ocupada por {existente_casilla.placa}.",
            "error"
        )
        return redirect(url_for('index'))

    fecha_ingreso = hora_colombia()
    if fecha_raw:
        try:
            fecha_ingreso = datetime.strptime(fecha_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("La fecha del vehículo no tiene un formato válido.", "error")
            return redirect(url_for('index'))

    vehiculo = Vehiculo(
        placa=placa,
        tipo=tipo,
        casilla=casilla,
        fecha_ingreso=fecha_ingreso,
        estado='activo',
        preexistente=True,
        lavado=False,
        costo_lavado=0,
        costo_servicios=0,
        metodo_pago='Efectivo',
        total_pagado=0
    )

    db.session.add(vehiculo)
    db.session.commit()

    firestore_guardar('vehiculos', vehiculo.id, {
        'id': vehiculo.id, 'placa': vehiculo.placa, 'nombre_cliente': vehiculo.nombre_cliente, 'telefono_cliente': vehiculo.telefono_cliente, 'tipo': vehiculo.tipo,
        'casilla': vehiculo.casilla, 'fecha_ingreso': vehiculo.fecha_ingreso.isoformat(),
        'fecha_salida': None, 'estado': vehiculo.estado, 'lavado': vehiculo.lavado,
        'costo_lavado': vehiculo.costo_lavado, 'costo_servicios': vehiculo.costo_servicios,
        'metodo_pago': vehiculo.metodo_pago, 'total_pagado': vehiculo.total_pagado,
        'preexistente': vehiculo.preexistente
    })

    flash(
        f"Vehículo {placa} registrado como preexistente en E-{casilla}.",
        "success"
    )
    return redirect(url_for('index'))


@app.route('/ingresar', methods=['POST'])
def ingresar():
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("Debes abrir la caja antes de registrar vehículos.", "error")
        return redirect(url_for('index'))

    placa = (request.form.get('placa') or '').upper().strip()
    nombre_cliente = (request.form.get('nombre_cliente') or '').strip()
    telefono_cliente = (request.form.get('telefono_cliente') or '').strip()
    tipo = request.form.get('tipo')

    try:
        casilla = int(request.form.get('casilla'))
    except (TypeError, ValueError):
        flash("La casilla no es válida.", "error")
        return redirect(url_for('index'))

    if not placa:
        flash("Debes ingresar una placa.", "error")
        return redirect(url_for('index'))
    if not nombre_cliente:
        flash("Debes ingresar el nombre del cliente.", "error")
        return redirect(url_for('index'))
    if not telefono_cliente:
        flash("Debes ingresar el número de teléfono del cliente.", "error")
        return redirect(url_for('index'))
    if casilla < 1 or casilla > 60:
        flash("La casilla debe estar entre E-1 y E-60.", "error")
        return redirect(url_for('index'))

    costo_servicios = limpiar_monto(request.form.get('costo_servicios'))
    if costo_servicios < 0:
        costo_servicios = 0

    existente_placa = Vehiculo.query.filter_by(placa=placa, estado='activo').first()
    if existente_placa:
        flash(f"El vehículo {placa} ya está dentro del parqueadero.", "error")
        return redirect(url_for('index'))

    existente = Vehiculo.query.filter_by(casilla=casilla, estado='activo').first()
    if existente:
        flash(f"La casilla E-{casilla} ya está ocupada por {existente.placa}.", "error")
        return redirect(url_for('index'))

    tarifa = Tarifa.query.filter_by(tipo=tipo).first()
    if not tarifa:
        flash("El tipo de vehículo seleccionado no tiene tarifa configurada.", "error")
        return redirect(url_for('index'))

    # Validación de zonas para evitar asignaciones incoherentes.
    rangos = {
        'moto': range(1, 11),
        'carro': range(11, 21),
        'volqueta': range(21, 31),
        'especial_a': range(31, 41),
        'especial_b': range(41, 51),
        'grande': range(51, 61),
    }
    if tipo in rangos and casilla not in rangos[tipo]:
        flash(f"La casilla E-{casilla} no corresponde a la zona de {tarifa.nombre}.", "error")
        return redirect(url_for('index'))

    fecha_raw = (request.form.get('fecha_ingreso') or '').strip()
    fecha_ingreso = hora_colombia()
    if fecha_raw:
        try:
            fecha_ingreso = datetime.strptime(fecha_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("La fecha/hora de ingreso no tiene un formato válido.", "error")
            return redirect(url_for('index'))

    nuevo = Vehiculo(
        placa=placa,
        nombre_cliente=nombre_cliente,
        telefono_cliente=telefono_cliente,
        tipo=tipo,
        casilla=casilla,
        lavado=False,
        costo_lavado=0,
        costo_servicios=costo_servicios,
        fecha_ingreso=fecha_ingreso
    )
    db.session.add(nuevo)
    db.session.commit()

    firestore_guardar('vehiculos', nuevo.id, {
        'id': nuevo.id, 'placa': nuevo.placa, 'nombre_cliente': nuevo.nombre_cliente, 'telefono_cliente': nuevo.telefono_cliente, 'tipo': nuevo.tipo,
        'casilla': nuevo.casilla, 'fecha_ingreso': nuevo.fecha_ingreso.isoformat(),
        'fecha_salida': None, 'estado': nuevo.estado, 'lavado': nuevo.lavado,
        'costo_lavado': nuevo.costo_lavado, 'costo_servicios': nuevo.costo_servicios,
        'metodo_pago': nuevo.metodo_pago, 'total_pagado': nuevo.total_pagado,
        'preexistente': nuevo.preexistente
    })

    flash(f"Vehículo {placa} ingresado en E-{casilla}.", "success")
    return redirect(url_for('index'))

@app.route('/comprobante_ingreso/<int:id>')
def comprobante_ingreso(id):
    vehiculo = db.session.get(Vehiculo, id)
    if not vehiculo:
        flash("No se encontró el registro del vehículo.", "error")
        return redirect(url_for('index'))

    # Factura/comprobante visible directamente en la interfaz.
    # No genera ni descarga PDF.
    return render_template_string("""
    <!doctype html>
    <html lang="es">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Comprobante - Favito POS</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { background:#111827; color:#f8fafc; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
            .factura { width:100%; max-width:430px; background:#fff; color:#111827; border-radius:18px; padding:28px; box-shadow:0 15px 45px rgba(0,0,0,.35); }
            .linea { border-bottom:1px dashed #9ca3af; padding:10px 0; }
            .etiqueta { color:#6b7280; font-size:.85rem; }
            .valor { font-weight:700; font-size:1.05rem; }
        </style>
    </head>
    <body>
        <div class="factura">
            <div class="text-center mb-3">
                <h2 class="fw-bold mb-1">FAVITO POS</h2>
                <div class="text-secondary">COMPROBANTE DE REGISTRO</div>
            </div>

            <div class="linea"><div class="etiqueta">Nombre del cliente</div><div class="valor">{{ vehiculo.nombre_cliente or '' }}</div></div>
            <div class="linea"><div class="etiqueta">Número de teléfono</div><div class="valor">{{ vehiculo.telefono_cliente or '' }}</div></div>
            <div class="linea"><div class="etiqueta">Placa</div><div class="valor">{{ vehiculo.placa or '' }}</div></div>
            <div class="linea"><div class="etiqueta">Fecha y hora de ingreso</div><div class="valor">{{ vehiculo.fecha_ingreso.strftime('%d/%m/%Y %H:%M') }}</div></div>

            <div class="d-grid gap-2 mt-4">
                <a href="{{ url_for('index') }}" class="btn btn-primary fw-bold">Volver al parqueadero</a>
            </div>
        </div>
    </body>
    </html>
    """, vehiculo=vehiculo)

@app.route('/salida/<int:id>', methods=['POST'])
def salida(id):
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("Debes abrir la caja para registrar cobros.", "error")
        return redirect(url_for('index'))

    vehiculo = db.session.get(Vehiculo, id)
    if not vehiculo or vehiculo.estado != 'activo':
        flash("El vehículo no está activo o ya fue cobrado.", "error")
        return redirect(url_for('index'))

    tipo_cobro = request.form.get('tipo_cobro', 'hora')
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    if metodo_pago not in ('Efectivo', 'Transferencia'):
        metodo_pago = 'Efectivo'

    tarifa = Tarifa.query.filter_by(tipo=vehiculo.tipo).first()
    if not tarifa:
        flash("No existe una tarifa para este vehículo.", "error")
        return redirect(url_for('index'))

    if tipo_cobro == 'mes':
        subtotal = tarifa.tarifa_mes
    else:
        ahora = hora_colombia()
        diff = ahora - vehiculo.fecha_ingreso
        horas = max(1, int(diff.total_seconds() // 3600))
        subtotal = horas * tarifa.tarifa_hora

    total = subtotal + vehiculo.costo_lavado + vehiculo.costo_servicios
    vehiculo.estado = 'salida'
    vehiculo.fecha_salida = hora_colombia()
    vehiculo.metodo_pago = metodo_pago
    vehiculo.total_pagado = total
    db.session.commit()

    firestore_guardar('vehiculos', vehiculo.id, {
        'id': vehiculo.id, 'placa': vehiculo.placa, 'nombre_cliente': vehiculo.nombre_cliente, 'telefono_cliente': vehiculo.telefono_cliente, 'tipo': vehiculo.tipo,
        'casilla': vehiculo.casilla, 'fecha_ingreso': vehiculo.fecha_ingreso.isoformat(),
        'fecha_salida': vehiculo.fecha_salida.isoformat() if vehiculo.fecha_salida else None,
        'estado': vehiculo.estado, 'lavado': vehiculo.lavado,
        'costo_lavado': vehiculo.costo_lavado, 'costo_servicios': vehiculo.costo_servicios,
        'metodo_pago': vehiculo.metodo_pago, 'total_pagado': vehiculo.total_pagado,
        'preexistente': vehiculo.preexistente
    })

    flash(f"Salida de {vehiculo.placa}. Total: ${total:,}.".replace(',', '.'), "success")
    return redirect(url_for('index'))


@app.route('/lavado/venta', methods=['POST'])
def lavado_venta():
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("Debes abrir la caja para registrar lavados.", "error")
        return redirect(url_for('index'))

    placa = (request.form.get('placa') or '').strip().upper()
    tipo_vehiculo = (request.form.get('tipo_vehiculo') or 'carro').strip()
    servicio = (request.form.get('servicio') or 'Lavado básico').strip()
    monto = limpiar_monto(request.form.get('monto'))
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    if metodo_pago not in ('Efectivo', 'Transferencia'):
        metodo_pago = 'Efectivo'

    fecha_raw = (request.form.get('fecha') or '').strip()
    fecha = hora_colombia()
    if fecha_raw:
        try:
            fecha = datetime.strptime(fecha_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("La fecha/hora del lavado no tiene un formato válido.", "error")
            return redirect(url_for('index'))

    if not placa:
        flash("Debes ingresar la placa del vehículo lavado.", "error")
        return redirect(url_for('index'))
    if monto <= 0:
        flash("El valor del lavado debe ser mayor que cero.", "error")
        return redirect(url_for('index'))

    venta = VentaLavado(
        placa=placa,
        tipo_vehiculo=tipo_vehiculo,
        servicio=servicio,
        monto=monto,
        metodo_pago=metodo_pago,
        fecha=fecha
    )
    db.session.add(venta)
    db.session.commit()
    firestore_guardar('ventas_lavado', venta.id, {
        'id': venta.id, 'placa': venta.placa, 'tipo_vehiculo': venta.tipo_vehiculo,
        'servicio': venta.servicio, 'monto': venta.monto, 'metodo_pago': venta.metodo_pago,
        'fecha': venta.fecha.isoformat()
    })
    flash(f"Lavado de {placa} registrado correctamente.", "success")
    return redirect(url_for('index'))

@app.route('/vulcanizadora/venta', methods=['POST'])
def vulcanizadora_venta():
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("Debes abrir la caja para registrar ventas.", "error")
        return redirect(url_for('index'))

    categoria = request.form.get('categoria')
    detalle_select = request.form.get('detalle_select')
    detalle_custom = request.form.get('detalle_custom')
    monto = limpiar_monto(request.form.get('monto'))
    metodo_pago = request.form.get('metodo_pago')

    servicio = detalle_custom if categoria == 'Otro' else f"{categoria} - {detalle_select}"

    venta = VentaVulcanizadora(servicio=servicio, monto=monto, metodo_pago=metodo_pago, fecha=hora_colombia())
    db.session.add(venta)
    db.session.commit()
    firestore_guardar('ventas_vulcanizadora', venta.id, {
        'id': venta.id, 'servicio': venta.servicio, 'monto': venta.monto,
        'metodo_pago': venta.metodo_pago, 'fecha': venta.fecha.isoformat()
    })
    flash("Venta de vulcanizadora registrada.", "success")
    return redirect(url_for('index'))

@app.route('/arriendos/pagar', methods=['POST'])
def arriendo_pagar():
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("Debes abrir la caja para registrar pagos.", "error")
        return redirect(url_for('index'))

    local = request.form.get('local')
    inquilino = request.form.get('inquilino')
    monto = limpiar_monto(request.form.get('monto'))
    metodo_pago = request.form.get('metodo_pago')

    arriendo = Arriendo(local=local, inquilino=inquilino, monto=monto, metodo_pago=metodo_pago, fecha=hora_colombia())
    db.session.add(arriendo)
    db.session.commit()
    firestore_guardar('arriendos', arriendo.id, {
        'id': arriendo.id, 'local': arriendo.local, 'inquilino': arriendo.inquilino,
        'monto': arriendo.monto, 'metodo_pago': arriendo.metodo_pago,
        'fecha': arriendo.fecha.isoformat()
    })
    flash("Pago de arriendo registrado con éxito.", "success")
    return redirect(url_for('index'))

@app.route('/gasto', methods=['POST'])
def registrar_gasto():
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("Debes abrir la caja para registrar gastos.", "error")
        return redirect(url_for('index'))

    descripcion = request.form.get('descripcion')
    monto = limpiar_monto(request.form.get('monto'))

    gasto = Gasto(descripcion=descripcion, monto=monto, fecha=hora_colombia())
    db.session.add(gasto)
    db.session.commit()
    firestore_guardar('gastos', gasto.id, {
        'id': gasto.id, 'descripcion': gasto.descripcion, 'monto': gasto.monto,
        'fecha': gasto.fecha.isoformat()
    })
    flash("Gasto registrado correctamente.", "success")
    return redirect(url_for('index'))

@app.route('/tarifas/actualizar/<int:id>', methods=['POST'])
def actualizar_tarifa(id):
    tarifa = Tarifa.query.get_or_404(id)
    tarifa.tarifa_hora = limpiar_monto(request.form.get('tarifa_hora'))
    tarifa.tarifa_lavado = limpiar_monto(request.form.get('tarifa_lavado'))
    tarifa.tarifa_mes = limpiar_monto(request.form.get('tarifa_mes'))
    db.session.commit()
    firestore_guardar('tarifas', tarifa.id, {
        'id': tarifa.id, 'tipo': tarifa.tipo, 'nombre': tarifa.nombre,
        'tarifa_hora': tarifa.tarifa_hora, 'tarifa_lavado': tarifa.tarifa_lavado,
        'tarifa_mes': tarifa.tarifa_mes
    })
    flash(f"Tarifas para {tarifa.nombre} actualizadas.", "success")
    return redirect(url_for('index'))

with app.app_context():
    db.create_all()
    # Migración simple para instalaciones SQLite existentes: agrega los nuevos campos
    # sin borrar los registros anteriores.
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    columnas_vehiculo = {col['name'] for col in inspector.get_columns('vehiculo')}
    if 'nombre_cliente' not in columnas_vehiculo:
        db.session.execute(text('ALTER TABLE vehiculo ADD COLUMN nombre_cliente VARCHAR(100)'))
    if 'telefono_cliente' not in columnas_vehiculo:
        db.session.execute(text('ALTER TABLE vehiculo ADD COLUMN telefono_cliente VARCHAR(30)'))
    db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
