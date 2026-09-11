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

class ClienteCuenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    telefono = db.Column(db.String(30), nullable=True)
    placa = db.Column(db.String(10), nullable=True)
    tipo_vehiculo = db.Column(db.String(50), nullable=True)
    observaciones = db.Column(db.String(300), nullable=True)
    tarifa_mensual = db.Column(db.Integer, default=0)
    dia_cobro = db.Column(db.Integer, default=1)
    activo = db.Column(db.Boolean, default=True)
    fecha_creacion = db.Column(db.DateTime, default=hora_colombia)

class MovimientoCuenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente_cuenta.id'), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)  # cargo / abono
    concepto = db.Column(db.String(200), nullable=False)
    monto = db.Column(db.Integer, nullable=False)
    metodo_pago = db.Column(db.String(20), nullable=True)
    fecha = db.Column(db.DateTime, default=hora_colombia)

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
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-cuentas"><i class="bi bi-journal-bookmark-fill me-1"></i> Cuentas Clientes</button></li>
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

        <div class="tab-pane fade" id="tab-cuentas">
            <div class="row g-3 mb-4">
                <div class="col-md-4">
                    <div class="stat-card">
                        <div><p>CLIENTES FIJOS</p><h2>{{ clientes_cuenta|length }}</h2></div>
                        <div class="stat-icon text-info"><i class="bi bi-people-fill"></i></div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="stat-card">
                        <div><p>CUENTAS PENDIENTES</p><h2 style="color: var(--red);">{{ cuentas_pendientes }}</h2></div>
                        <div class="stat-icon text-danger"><i class="bi bi-exclamation-circle-fill"></i></div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="stat-card">
                        <div><p>TOTAL POR COBRAR</p><h2 style="color: var(--red);">${{ "{:,}".format(total_por_cobrar).replace(',', '.') }}</h2></div>
                        <div class="stat-icon text-warning"><i class="bi bi-cash-stack"></i></div>
                    </div>
                </div>
            </div>

            <div class="row">
                <div class="col-lg-4">
                    <div class="panel-card">
                        <h5 class="fw-bold mb-3 text-info"><i class="bi bi-person-plus-fill me-2"></i> Nuevo Cliente Fijo</h5>
                        <form action="/cuentas/cliente/nuevo" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Nombre del Cliente</label>
                                <input type="text" name="nombre" class="form-control" maxlength="120" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Teléfono</label>
                                <input type="tel" name="telefono" class="form-control" maxlength="30">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Placa</label>
                                <input type="text" name="placa" class="form-control text-uppercase" maxlength="10">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Tipo de Vehículo</label>
                                <select name="tipo_vehiculo" class="form-select">
                                    <option value="">No especificado</option>
                                    {% for t in tarifas %}
                                        <option value="{{ t.nombre }}">{{ t.nombre }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Observaciones</label>
                                <textarea name="observaciones" class="form-control" rows="3" maxlength="300" placeholder="Ej: Cliente mensual, empresa, etc."></textarea>
                            </div>
                            <div class="row g-2 mb-3">
                                <div class="col-7">
                                    <label class="form-label">Cuota mensual ($)</label>
                                    <input type="text" name="tarifa_mensual" class="form-control" placeholder="Ej: 90.000">
                                </div>
                                <div class="col-5">
                                    <label class="form-label">Día de cobro</label>
                                    <input type="number" name="dia_cobro" class="form-control" min="1" max="31" value="1">
                                </div>
                            </div>
                            <button type="submit" class="btn btn-info w-100 fw-bold">
                                <i class="bi bi-save me-1"></i> Crear Cuenta
                            </button>
                        </form>
                    </div>

                    <div class="panel-card">
                        <h5 class="fw-bold mb-3 text-warning"><i class="bi bi-plus-circle me-2"></i> Registrar Cargo</h5>
                        <p class="text-muted small">El cargo queda como deuda y <strong>NO entra a caja</strong> hasta que el cliente haga un abono.</p>
                        <form action="/cuentas/cargo" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Cliente</label>
                                <select name="cliente_id" class="form-select" required>
                                    <option value="">Seleccionar cliente...</option>
                                    {% for c in clientes_cuenta %}
                                        <option value="{{ c.id }}">{{ c.nombre }}{% if c.placa %} — {{ c.placa }}{% endif %}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Concepto</label>
                                <input type="text" name="concepto" class="form-control" maxlength="200" placeholder="Ej: Parqueadero semana" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Valor ($)</label>
                                <input type="text" name="monto" class="form-control" placeholder="Ej: 50.000" required>
                            </div>
                            <button type="submit" class="btn btn-warning w-100 fw-bold text-dark">
                                <i class="bi bi-journal-plus me-1"></i> Agregar a Cuenta
                            </button>
                        </form>
                    </div>
                </div>

                <div class="col-lg-8">
                    <div class="panel-card border border-warning">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <div>
                                <h5 class="fw-bold m-0 text-warning"><i class="bi bi-clipboard-check me-2"></i> LISTA DE COBRO — CLIENTES FIJOS</h5>
                                <small class="text-muted">Aquí ves quién debe pagar este mes, cuánto cobrar y si ya se registró el cobro.</small>
                            </div>
                            <span class="badge bg-warning text-dark">{{ mes_actual }}</span>
                        </div>
                        <div class="table-responsive">
                            <table class="table table-custom align-middle">
                                <thead><tr><th>Cliente</th><th>Placa</th><th>Cuota</th><th>Último abono</th><th>Saldo</th><th>Estado</th><th>Cobro</th></tr></thead>
                                <tbody>
                                {% for c in clientes_cuenta %}
                                    {% set info = lista_cobro.get(c.id) %}
                                    <tr>
                                        <td><strong>{{ c.nombre }}</strong><br><small class="text-muted">Día {{ c.dia_cobro or 1 }}</small></td>
                                        <td class="font-monospace text-info fw-bold">{{ c.placa or '—' }}</td>
                                        <td class="fw-bold">${{ "{:,}".format(c.tarifa_mensual or 0).replace(',', '.') }}</td>
                                        <td>{{ info.ultimo_abono.strftime('%d/%m/%Y') if info and info.ultimo_abono else 'Nunca' }}</td>
                                        <td class="fw-bold text-danger">${{ "{:,}".format(info.saldo if info else 0).replace(',', '.') }}</td>
                                        <td>
                                            {% set dia_programado = [c.dia_cobro or 1, 28 if false else 31]|min %}
                                            {% if info and info.cobro_mes %}<span class="badge bg-danger">Por cobrar</span>
                                            {% elif (c.tarifa_mensual or 0) > 0 and ahora.day >= (c.dia_cobro or 1) %}<span class="badge bg-danger">Por cobrar</span>
                                            {% elif (c.tarifa_mensual or 0) > 0 %}<span class="badge bg-warning text-dark">Próximo cobro</span>
                                            {% else %}<span class="badge bg-secondary">Sin cuota</span>{% endif %}
                                        </td>
                                        <td>
                                            {% if (c.tarifa_mensual or 0) > 0 and not (info and info.cobro_mes) %}
                                            <form action="/cuentas/cobro_mensual" method="POST">
                                                <input type="hidden" name="cliente_id" value="{{ c.id }}">
                                                <button class="btn btn-warning btn-sm fw-bold text-dark"><i class="bi bi-cash-coin me-1"></i> Generar cobro</button>
                                            </form>
                                            {% else %}<span class="text-muted small">Revisar cuenta</span>{% endif %}
                                        </td>
                                    </tr>
                                {% else %}
                                    <tr><td colspan="7" class="text-center text-muted py-4">No hay clientes fijos registrados.</td></tr>
                                {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div class="panel-card">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <h5 class="fw-bold m-0"><i class="bi bi-people text-info me-2"></i> Clientes Fijos y Estado de Cuenta</h5>
                            <input type="text" id="buscarCuenta" class="form-control" style="max-width:280px" placeholder="🔍 Buscar nombre o placa...">
                        </div>
                        <div class="table-responsive">
                            <table class="table table-custom align-middle" id="tablaCuentas">
                                <thead>
                                    <tr>
                                        <th>Cliente</th>
                                        <th>Teléfono</th>
                                        <th>Placa</th>
                                        <th>Saldo</th>
                                        <th>Estado</th>
                                        <th>Acciones</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for c in clientes_cuenta %}
                                    <tr class="fila-cuenta">
                                        <td>
                                            <div class="fw-bold">{{ c.nombre }}</div>
                                            {% if c.observaciones %}<small class="text-muted">{{ c.observaciones }}</small>{% endif %}
                                        </td>
                                        <td>{{ c.telefono or '—' }}</td>
                                        <td class="font-monospace fw-bold text-info">{{ c.placa or '—' }}</td>
                                        <td class="fw-bold {% if saldos_cuenta.get(c.id, 0) > 0 %}text-danger{% else %}text-success{% endif %}">
                                            ${{ "{:,}".format(saldos_cuenta.get(c.id, 0)).replace(',', '.') }}
                                        </td>
                                        <td>
                                            {% if saldos_cuenta.get(c.id, 0) > 0 %}
                                                <span class="badge bg-danger">Pendiente</span>
                                            {% else %}
                                                <span class="badge bg-success">Al día</span>
                                            {% endif %}
                                        </td>
                                        <td>
                                            <button type="button" class="btn btn-outline-info btn-sm" data-bs-toggle="modal" data-bs-target="#modalCuenta{{ c.id }}">
                                                <i class="bi bi-eye me-1"></i> Ver
                                            </button>
                                        </td>
                                    </tr>

                                    <div class="modal fade" id="modalCuenta{{ c.id }}" tabindex="-1">
                                        <div class="modal-dialog modal-lg">
                                            <div class="modal-content bg-dark text-light border border-secondary">
                                                <div class="modal-header border-secondary">
                                                    <div>
                                                        <h5 class="modal-title fw-bold">{{ c.nombre }}</h5>
                                                        <small class="text-muted">{{ c.telefono or 'Sin teléfono' }}{% if c.placa %} · {{ c.placa }}{% endif %}</small>
                                                    </div>
                                                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                                                </div>
                                                <div class="modal-body">
                                                    <div class="row g-3 mb-3">
                                                        <div class="col-md-4"><div class="p-3 rounded border border-danger"><small class="text-muted d-block">SALDO</small><strong class="fs-4 text-danger">${{ "{:,}".format(saldos_cuenta.get(c.id, 0)).replace(',', '.') }}</strong></div></div>
                                                        <div class="col-md-4"><div class="p-3 rounded border border-secondary"><small class="text-muted d-block">CLIENTE</small><strong>{{ c.nombre }}</strong></div></div>
                                                        <div class="col-md-4"><div class="p-3 rounded border border-secondary"><small class="text-muted d-block">PLACA</small><strong>{{ c.placa or '—' }}</strong></div></div>
                                                    </div>

                                                    <div class="d-flex justify-content-end gap-2 mb-3">
                                                        <button type="button" class="btn btn-outline-info btn-sm fw-bold" data-bs-toggle="collapse" data-bs-target="#editarCuenta{{ c.id }}">
                                                            <i class="bi bi-pencil-square me-1"></i> Editar datos
                                                        </button>
                                                        <form action="/cuentas/cliente/{{ c.id }}/eliminar" method="POST" onsubmit="return confirm('¿Eliminar definitivamente esta cuenta y TODOS sus cargos y abonos? Esta acción no se puede deshacer.');">
                                                            <button type="submit" class="btn btn-outline-danger btn-sm fw-bold">
                                                                <i class="bi bi-trash me-1"></i> Eliminar cuenta
                                                            </button>
                                                        </form>
                                                    </div>

                                                    <div class="collapse mb-3" id="editarCuenta{{ c.id }}">
                                                        <div class="panel-card border border-info">
                                                            <h6 class="fw-bold text-info mb-3"><i class="bi bi-pencil-square me-1"></i> Editar cliente</h6>
                                                            <form action="/cuentas/cliente/{{ c.id }}/editar" method="POST">
                                                                <div class="row g-2">
                                                                    <div class="col-md-6">
                                                                        <label class="form-label">Nombre</label>
                                                                        <input type="text" name="nombre" class="form-control" maxlength="120" value="{{ c.nombre }}" required>
                                                                    </div>
                                                                    <div class="col-md-6">
                                                                        <label class="form-label">Teléfono</label>
                                                                        <input type="tel" name="telefono" class="form-control" maxlength="30" value="{{ c.telefono or '' }}">
                                                                    </div>
                                                                    <div class="col-md-4">
                                                                        <label class="form-label">Placa</label>
                                                                        <input type="text" name="placa" class="form-control text-uppercase" maxlength="10" value="{{ c.placa or '' }}">
                                                                    </div>
                                                                    <div class="col-md-4">
                                                                        <label class="form-label">Tipo de vehículo</label>
                                                                        <select name="tipo_vehiculo" class="form-select">
                                                                            <option value="">No especificado</option>
                                                                            {% for t in tarifas %}<option value="{{ t.nombre }}" {% if c.tipo_vehiculo == t.nombre %}selected{% endif %}>{{ t.nombre }}</option>{% endfor %}
                                                                        </select>
                                                                    </div>
                                                                    <div class="col-md-4">
                                                                        <label class="form-label">Cuota mensual ($)</label>
                                                                        <input type="text" name="tarifa_mensual" class="form-control" value="{{ c.tarifa_mensual or 0 }}">
                                                                    </div>
                                                                    <div class="col-md-4">
                                                                        <label class="form-label">Día de cobro</label>
                                                                        <input type="number" name="dia_cobro" class="form-control" min="1" max="31" value="{{ c.dia_cobro or 1 }}">
                                                                    </div>
                                                                    <div class="col-md-8">
                                                                        <label class="form-label">Observaciones</label>
                                                                        <input type="text" name="observaciones" class="form-control" maxlength="300" value="{{ c.observaciones or '' }}">
                                                                    </div>
                                                                </div>
                                                                <button type="submit" class="btn btn-info fw-bold mt-3"><i class="bi bi-check-circle me-1"></i> Guardar cambios</button>
                                                            </form>
                                                        </div>
                                                    </div>

                                                    <div class="panel-card mb-3">
                                                        <h6 class="fw-bold mb-3"><i class="bi bi-cash-coin text-success me-1"></i> Registrar Abono / Pago</h6>
                                                        <form action="/cuentas/abono" method="POST" class="row g-2">
                                                            <input type="hidden" name="cliente_id" value="{{ c.id }}">
                                                            <div class="col-md-4">
                                                                <input type="text" name="monto" class="form-control" placeholder="Valor $ 30.000" required>
                                                            </div>
                                                            <div class="col-md-4">
                                                                <select name="metodo_pago" class="form-select">
                                                                    <option value="Efectivo">Efectivo</option>
                                                                    <option value="Transferencia">Transferencia</option>
                                                                </select>
                                                            </div>
                                                            <div class="col-md-4">
                                                                <button class="btn btn-success w-100 fw-bold"><i class="bi bi-check-circle me-1"></i> Registrar Abono</button>
                                                            </div>
                                                        </form>
                                                    </div>

                                                    <h6 class="fw-bold mb-2">Historial de la Cuenta</h6>
                                                    <div class="table-responsive">
                                                        <table class="table table-custom">
                                                            <thead><tr><th>Fecha</th><th>Tipo</th><th>Concepto</th><th>Método</th><th>Valor</th></tr></thead>
                                                            <tbody>
                                                                {% for m in movimientos_cuenta.get(c.id, []) %}
                                                                <tr>
                                                                    <td>{{ m.fecha.strftime('%d/%m/%Y %H:%M') }}</td>
                                                                    <td>
                                                                        {% if m.tipo == 'cargo' %}
                                                                            <span class="badge bg-danger">Cargo</span>
                                                                        {% else %}
                                                                            <span class="badge bg-success">Abono</span>
                                                                        {% endif %}
                                                                    </td>
                                                                    <td>{{ m.concepto }}</td>
                                                                    <td>{{ m.metodo_pago or '—' }}</td>
                                                                    <td class="fw-bold {% if m.tipo == 'cargo' %}text-danger{% else %}text-success{% endif %}">
                                                                        {{ '+' if m.tipo == 'cargo' else '-' }}${{ "{:,}".format(m.monto).replace(',', '.') }}
                                                                    </td>
                                                                </tr>
                                                                {% else %}
                                                                <tr><td colspan="5" class="text-center text-muted py-4">No hay movimientos todavía.</td></tr>
                                                                {% endfor %}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    {% endfor %}
                                </tbody>
                            </table>
                            {% if not clientes_cuenta %}
                                <p class="text-center text-muted py-4 mb-0">Todavía no hay clientes fijos registrados.</p>
                            {% endif %}
                        </div>
                    </div>
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

            const buscadorCuenta = document.getElementById('buscarCuenta');
            if (buscadorCuenta) {
                buscadorCuenta.addEventListener('input', function() {
                    const texto = this.value.toLowerCase().trim();
                    document.querySelectorAll('#tablaCuentas .fila-cuenta').forEach(function(fila) {
                        fila.style.display = fila.innerText.toLowerCase().includes(texto) ? '' : 'none';
                    });
                });
            }

            function abrirPestanaDesdeHash() {
                if (window.location.hash === '#tab-cuentas') {
                    const boton = document.querySelector('[data-bs-target="#tab-cuentas"]');
                    if (boton) bootstrap.Tab.getOrCreateInstance(boton).show();
                }
            }
            abrirPestanaDesdeHash();
            window.addEventListener('hashchange', abrirPestanaDesdeHash);
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

    clientes_cuenta = ClienteCuenta.query.filter_by(activo=True).order_by(ClienteCuenta.nombre).all()
    movimientos_todos = MovimientoCuenta.query.order_by(MovimientoCuenta.fecha.desc()).all()
    movimientos_cuenta = {c.id: [] for c in clientes_cuenta}
    saldos_cuenta = {c.id: 0 for c in clientes_cuenta}
    for mov in movimientos_todos:
        if mov.cliente_id in movimientos_cuenta:
            movimientos_cuenta[mov.cliente_id].append(mov)
            if mov.tipo == 'cargo':
                saldos_cuenta[mov.cliente_id] += mov.monto
            elif mov.tipo == 'abono':
                saldos_cuenta[mov.cliente_id] -= mov.monto
    # Un saldo nunca se muestra negativo; los pagos de más quedan registrados,
    # pero el cliente se considera al día.
    for cid in saldos_cuenta:
        saldos_cuenta[cid] = max(0, saldos_cuenta[cid])
    ahora = hora_colombia()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if ahora.month == 12:
        siguiente_mes = ahora.replace(year=ahora.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        siguiente_mes = ahora.replace(month=ahora.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)

    # Generación automática de la cuota mensual: al llegar el día de cobro,
    # si todavía no existe la cuota de este mes, se crea como deuda.
    import calendar
    dia_actual = ahora.day
    ultimo_dia_mes = calendar.monthrange(ahora.year, ahora.month)[1]
    for c in clientes_cuenta:
        dia_programado = min(max(1, c.dia_cobro or 1), ultimo_dia_mes)
        movs = movimientos_cuenta.get(c.id, [])
        cobro_mes = next((m for m in movs if m.tipo == 'cargo' and m.fecha >= inicio_mes and m.fecha < siguiente_mes and m.concepto.startswith('Cuota mensual')), None)
        if dia_actual >= dia_programado and (c.tarifa_mensual or 0) > 0 and not cobro_mes:
            movimiento = MovimientoCuenta(
                cliente_id=c.id, tipo='cargo',
                concepto=f"Cuota mensual - {inicio_mes.strftime('%m/%Y')}",
                monto=c.tarifa_mensual, metodo_pago=None, fecha=ahora
            )
            db.session.add(movimiento)
            db.session.flush()
            firestore_guardar('movimientos_cuenta', movimiento.id, {
                'id': movimiento.id, 'cliente_id': movimiento.cliente_id, 'tipo': movimiento.tipo,
                'concepto': movimiento.concepto, 'monto': movimiento.monto, 'metodo_pago': None,
                'fecha': movimiento.fecha.isoformat()
            })
            movs.insert(0, movimiento)
            movimientos_cuenta[c.id] = movs
    db.session.commit()

    lista_cobro = {}
    for c in clientes_cuenta:
        movs = movimientos_cuenta.get(c.id, [])
        cobro_mes = next((m for m in movs if m.tipo == 'cargo' and m.fecha >= inicio_mes and m.fecha < siguiente_mes and m.concepto.startswith('Cuota mensual')), None)
        ultimo_abono = next((m.fecha for m in movs if m.tipo == 'abono'), None)
        lista_cobro[c.id] = type('InfoCobro', (), {
            'cobro_mes': cobro_mes,
            'ultimo_abono': ultimo_abono,
            'saldo': saldos_cuenta.get(c.id, 0)
        })()

    meses = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
    mes_actual = f"{meses[ahora.month - 1]} {ahora.year}"
    cuentas_pendientes = sum(1 for saldo in saldos_cuenta.values() if saldo > 0)
    total_por_cobrar = sum(saldos_cuenta.values())

    abonos_cuenta = MovimientoCuenta.query.filter(
        MovimientoCuenta.tipo == 'abono',
        MovimientoCuenta.fecha >= desde
    ).all()

    total_ventas = (
        sum(v.total_pagado for v in vehiculos_cobrados)
        + sum(v.monto for v in ventas_vulc)
        + sum(v.monto for v in ventas_lavado)
        + sum(a.monto for a in lista_arriendos)
        + sum(m.monto for m in abonos_cuenta)
    )
    total_gastos = sum(g.monto for g in lista_gastos)

    total_efectivo = (
        sum(v.total_pagado for v in vehiculos_cobrados if v.metodo_pago == 'Efectivo')
        + sum(v.monto for v in ventas_vulc if v.metodo_pago == 'Efectivo')
        + sum(v.monto for v in ventas_lavado if v.metodo_pago == 'Efectivo')
        + sum(a.monto for a in lista_arriendos if a.metodo_pago == 'Efectivo')
        + sum(m.monto for m in abonos_cuenta if m.metodo_pago == 'Efectivo')
    )
    total_transferencia = (
        sum(v.total_pagado for v in vehiculos_cobrados if v.metodo_pago == 'Transferencia')
        + sum(v.monto for v in ventas_vulc if v.metodo_pago == 'Transferencia')
        + sum(v.monto for v in ventas_lavado if v.metodo_pago == 'Transferencia')
        + sum(a.monto for a in lista_arriendos if a.metodo_pago == 'Transferencia')
        + sum(m.monto for m in abonos_cuenta if m.metodo_pago == 'Transferencia')
    )

    return render_template_string(
        HTML_TEMPLATE,
        caja=caja,
        tarifas=tarifas,
        ahora=ahora,
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
        clientes_cuenta=clientes_cuenta,
        movimientos_cuenta=movimientos_cuenta,
        saldos_cuenta=saldos_cuenta,
        cuentas_pendientes=cuentas_pendientes,
        total_por_cobrar=total_por_cobrar,
        lista_cobro=lista_cobro,
        mes_actual=mes_actual,
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

    abonos_cuenta_cierre = MovimientoCuenta.query.filter(
        MovimientoCuenta.tipo == 'abono',
        MovimientoCuenta.fecha >= desde,
        MovimientoCuenta.fecha <= ahora
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
    efectivo_abonos_cuenta = sum(m.monto for m in abonos_cuenta_cierre if m.metodo_pago == 'Efectivo')
    transferencia_abonos_cuenta = sum(m.monto for m in abonos_cuenta_cierre if m.metodo_pago == 'Transferencia')

    efectivo = efectivo_parqueadero + efectivo_vulc + efectivo_lavados + efectivo_arriendos + efectivo_abonos_cuenta
    transferencia = transferencia_parqueadero + transferencia_vulc + transferencia_lavados + transferencia_arriendos + transferencia_abonos_cuenta
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
    escribir("Cuentas clientes - abonos efectivo: " + dinero(efectivo_abonos_cuenta))
    escribir("Cuentas clientes - abonos transferencia: " + dinero(transferencia_abonos_cuenta))
    escribir("TOTAL INGRESOS: " + dinero(total_ingresos), 20, True)
    escribir("TOTAL GASTOS: " + dinero(total_gastos), 20, True)
    escribir("EFECTIVO ESPERADO EN CAJA: " + dinero(efectivo_esperado), 24, True, 12)

    escribir("OPERACIONES DEL TURNO", 20, True, 12)
    escribir("Vehículos cobrados: " + str(len(vehiculos)))
    escribir("Servicios de vulcanizadora: " + str(len(vulc)))
    escribir("Servicios de lavado: " + str(len(lavados)))
    escribir("Pagos de arriendo: " + str(len(arriendos)))
    escribir("Abonos de cuentas: " + str(len(abonos_cuenta_cierre)))
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
    for m in abonos_cuenta_cierre:
        cliente = db.session.get(ClienteCuenta, m.cliente_id)
        nombre_cliente = cliente.nombre if cliente else f"Cliente {m.cliente_id}"
        escribir(f"Cuenta cliente | {nombre_cliente} | Abono | {m.metodo_pago} | {dinero(m.monto)}", 15, False, 9)

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

@app.route('/cuentas/cliente/nuevo', methods=['POST'])
def cuenta_cliente_nuevo():
    nombre = (request.form.get('nombre') or '').strip()
    telefono = (request.form.get('telefono') or '').strip()
    placa = (request.form.get('placa') or '').strip().upper()
    tipo_vehiculo = (request.form.get('tipo_vehiculo') or '').strip()
    observaciones = (request.form.get('observaciones') or '').strip()
    tarifa_mensual = limpiar_monto(request.form.get('tarifa_mensual'))
    try:
        dia_cobro = int(request.form.get('dia_cobro') or 1)
    except (TypeError, ValueError):
        dia_cobro = 1
    dia_cobro = min(31, max(1, dia_cobro))

    if not nombre:
        flash("Debes ingresar el nombre del cliente.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    cliente = ClienteCuenta(
        nombre=nombre,
        telefono=telefono,
        placa=placa or None,
        tipo_vehiculo=tipo_vehiculo or None,
        observaciones=observaciones or None,
        tarifa_mensual=max(0, tarifa_mensual),
        dia_cobro=dia_cobro,
        activo=True
    )
    db.session.add(cliente)
    db.session.commit()

    firestore_guardar('clientes_cuenta', cliente.id, {
        'id': cliente.id,
        'nombre': cliente.nombre,
        'telefono': cliente.telefono,
        'placa': cliente.placa,
        'tipo_vehiculo': cliente.tipo_vehiculo,
        'observaciones': cliente.observaciones,
        'tarifa_mensual': cliente.tarifa_mensual,
        'dia_cobro': cliente.dia_cobro,
        'activo': cliente.activo,
        'fecha_creacion': cliente.fecha_creacion.isoformat()
    })

    flash(f"Cuenta creada para {nombre}.", "success")
    return redirect(url_for('index') + '#tab-cuentas')


@app.route('/cuentas/cliente/<int:id>/editar', methods=['POST'])
def cuenta_cliente_editar(id):
    cliente = db.session.get(ClienteCuenta, id)
    if not cliente:
        flash("Cliente no encontrado.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    nombre = (request.form.get('nombre') or '').strip()
    telefono = (request.form.get('telefono') or '').strip()
    placa = (request.form.get('placa') or '').strip().upper()
    tipo_vehiculo = (request.form.get('tipo_vehiculo') or '').strip()
    observaciones = (request.form.get('observaciones') or '').strip()
    tarifa_mensual = limpiar_monto(request.form.get('tarifa_mensual'))
    try:
        dia_cobro = int(request.form.get('dia_cobro') or 1)
    except (TypeError, ValueError):
        dia_cobro = 1
    dia_cobro = min(31, max(1, dia_cobro))

    if not nombre:
        flash("El nombre del cliente es obligatorio.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    cliente.nombre = nombre
    cliente.telefono = telefono or None
    cliente.placa = placa or None
    cliente.tipo_vehiculo = tipo_vehiculo or None
    cliente.observaciones = observaciones or None
    cliente.tarifa_mensual = max(0, tarifa_mensual)
    cliente.dia_cobro = dia_cobro
    db.session.commit()

    firestore_guardar('clientes_cuenta', cliente.id, {
        'id': cliente.id, 'nombre': cliente.nombre, 'telefono': cliente.telefono,
        'placa': cliente.placa, 'tipo_vehiculo': cliente.tipo_vehiculo,
        'observaciones': cliente.observaciones, 'tarifa_mensual': cliente.tarifa_mensual,
        'dia_cobro': cliente.dia_cobro, 'activo': cliente.activo,
        'fecha_creacion': cliente.fecha_creacion.isoformat() if cliente.fecha_creacion else None
    })
    flash(f"Datos de {cliente.nombre} actualizados correctamente. El historial de cobros y pagos se conserva.", "success")
    return redirect(url_for('index') + '#tab-cuentas')


@app.route('/cuentas/cobro_mensual', methods=['POST'])
def cuenta_cobro_mensual():
    cliente_id = request.form.get('cliente_id')
    try:
        cliente_id = int(cliente_id)
    except (TypeError, ValueError):
        cliente_id = 0
    cliente = db.session.get(ClienteCuenta, cliente_id)
    if not cliente or not cliente.activo:
        flash("El cliente seleccionado no existe.", "error")
        return redirect(url_for('index') + '#tab-cuentas')
    monto = cliente.tarifa_mensual or 0
    if monto <= 0:
        flash("Este cliente no tiene una cuota mensual configurada.", "error")
        return redirect(url_for('index') + '#tab-cuentas')
    ahora = hora_colombia()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if ahora.month == 12:
        siguiente_mes = ahora.replace(year=ahora.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        siguiente_mes = ahora.replace(month=ahora.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    existente = MovimientoCuenta.query.filter(
        MovimientoCuenta.cliente_id == cliente.id,
        MovimientoCuenta.tipo == 'cargo',
        MovimientoCuenta.fecha >= inicio_mes,
        MovimientoCuenta.fecha < siguiente_mes,
        MovimientoCuenta.concepto.like('Cuota mensual%')
    ).first()
    if existente:
        flash(f"El cobro mensual de {cliente.nombre} ya fue generado este mes.", "error")
        return redirect(url_for('index') + '#tab-cuentas')
    concepto = f"Cuota mensual - {inicio_mes.strftime('%m/%Y')}"
    movimiento = MovimientoCuenta(cliente_id=cliente.id, tipo='cargo', concepto=concepto, monto=monto, metodo_pago=None, fecha=ahora)
    db.session.add(movimiento)
    db.session.commit()
    firestore_guardar('movimientos_cuenta', movimiento.id, {
        'id': movimiento.id, 'cliente_id': movimiento.cliente_id, 'tipo': movimiento.tipo,
        'concepto': movimiento.concepto, 'monto': movimiento.monto, 'metodo_pago': None,
        'fecha': movimiento.fecha.isoformat()
    })
    flash(f"Cobro mensual generado para {cliente.nombre}: ${monto:,}.".replace(',', '.'), "success")
    return redirect(url_for('index') + '#tab-cuentas')

@app.route('/cuentas/cargo', methods=['POST'])
def cuenta_cargo():
    cliente_id = request.form.get('cliente_id')
    concepto = (request.form.get('concepto') or '').strip()
    monto = limpiar_monto(request.form.get('monto'))

    try:
        cliente_id = int(cliente_id)
    except (TypeError, ValueError):
        cliente_id = 0

    cliente = db.session.get(ClienteCuenta, cliente_id)
    if not cliente or not cliente.activo:
        flash("El cliente seleccionado no existe.", "error")
        return redirect(url_for('index') + '#tab-cuentas')
    if not concepto:
        flash("Debes indicar el concepto del cargo.", "error")
        return redirect(url_for('index') + '#tab-cuentas')
    if monto <= 0:
        flash("El valor del cargo debe ser mayor que cero.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    movimiento = MovimientoCuenta(
        cliente_id=cliente.id,
        tipo='cargo',
        concepto=concepto,
        monto=monto,
        metodo_pago=None,
        fecha=hora_colombia()
    )
    db.session.add(movimiento)
    db.session.commit()

    firestore_guardar('movimientos_cuenta', movimiento.id, {
        'id': movimiento.id,
        'cliente_id': movimiento.cliente_id,
        'tipo': movimiento.tipo,
        'concepto': movimiento.concepto,
        'monto': movimiento.monto,
        'metodo_pago': None,
        'fecha': movimiento.fecha.isoformat()
    })

    flash(f"Cargo de ${monto:,} agregado a la cuenta de {cliente.nombre}.".replace(',', '.'), "success")
    return redirect(url_for('index') + '#tab-cuentas')


@app.route('/cuentas/abono', methods=['POST'])
def cuenta_abono():
    caja = Caja.query.first()
    if not caja or caja.estado != 'abierta':
        flash("Debes abrir la caja antes de registrar un abono.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    cliente_id = request.form.get('cliente_id')
    monto = limpiar_monto(request.form.get('monto'))
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    if metodo_pago not in ('Efectivo', 'Transferencia'):
        metodo_pago = 'Efectivo'

    try:
        cliente_id = int(cliente_id)
    except (TypeError, ValueError):
        cliente_id = 0

    cliente = db.session.get(ClienteCuenta, cliente_id)
    if not cliente or not cliente.activo:
        flash("El cliente seleccionado no existe.", "error")
        return redirect(url_for('index') + '#tab-cuentas')
    if monto <= 0:
        flash("El valor del abono debe ser mayor que cero.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    # El abono sí es un ingreso real y se tendrá en cuenta en el cierre de caja.
    movimiento = MovimientoCuenta(
        cliente_id=cliente.id,
        tipo='abono',
        concepto='Abono a cuenta',
        monto=monto,
        metodo_pago=metodo_pago,
        fecha=hora_colombia()
    )
    db.session.add(movimiento)
    db.session.commit()

    firestore_guardar('movimientos_cuenta', movimiento.id, {
        'id': movimiento.id,
        'cliente_id': movimiento.cliente_id,
        'tipo': movimiento.tipo,
        'concepto': movimiento.concepto,
        'monto': movimiento.monto,
        'metodo_pago': movimiento.metodo_pago,
        'fecha': movimiento.fecha.isoformat()
    })

    flash(f"Abono de ${monto:,} registrado para {cliente.nombre}.".replace(',', '.'), "success")
    return redirect(url_for('index') + '#tab-cuentas')


@app.route('/cuentas/cliente/<int:id>/eliminar', methods=['POST'])
def cuenta_cliente_eliminar(id):
    cliente = db.session.get(ClienteCuenta, id)
    if not cliente:
        flash("Cliente no encontrado.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    nombre = cliente.nombre
    movimientos = MovimientoCuenta.query.filter_by(cliente_id=cliente.id).all()

    # Eliminar primero los movimientos para no dejar registros huérfanos.
    for movimiento in movimientos:
        firestore_eliminar('movimientos_cuenta', movimiento.id)
        db.session.delete(movimiento)

    firestore_eliminar('clientes_cuenta', cliente.id)
    db.session.delete(cliente)
    db.session.commit()

    flash(f"Cuenta de {nombre} y sus cargos/abonos fueron eliminados correctamente.", "success")
    return redirect(url_for('index') + '#tab-cuentas')


@app.route('/cuentas/cliente/<int:id>/desactivar', methods=['POST'])
def cuenta_cliente_desactivar(id):
    cliente = db.session.get(ClienteCuenta, id)
    if not cliente:
        flash("Cliente no encontrado.", "error")
        return redirect(url_for('index') + '#tab-cuentas')

    cliente.activo = False
    db.session.commit()
    firestore_guardar('clientes_cuenta', cliente.id, {
        'id': cliente.id,
        'nombre': cliente.nombre,
        'telefono': cliente.telefono,
        'placa': cliente.placa,
        'tipo_vehiculo': cliente.tipo_vehiculo,
        'observaciones': cliente.observaciones,
        'activo': cliente.activo,
        'fecha_creacion': cliente.fecha_creacion.isoformat() if cliente.fecha_creacion else None
    })
    flash(f"Cuenta de {cliente.nombre} archivada. El historial se conserva.", "success")
    return redirect(url_for('index') + '#tab-cuentas')


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
    columnas_cliente = {col['name'] for col in inspector.get_columns('cliente_cuenta')}
    if 'tarifa_mensual' not in columnas_cliente:
        db.session.execute(text('ALTER TABLE cliente_cuenta ADD COLUMN tarifa_mensual INTEGER DEFAULT 0'))
    if 'dia_cobro' not in columnas_cliente:
        db.session.execute(text('ALTER TABLE cliente_cuenta ADD COLUMN dia_cobro INTEGER DEFAULT 1'))
    db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
