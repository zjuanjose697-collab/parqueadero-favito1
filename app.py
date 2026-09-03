import os
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, redirect, url_for, flash, render_template_string
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = "favito_pos_pro_secret_2026"

# Configuración de Zona Horaria para Colombia (UTC-5)
COLOMBIA_TZ = ZoneInfo("America/Bogota")

def hora_colombia():
    return datetime.now(COLOMBIA_TZ)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'parqueadero.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

def actualizar_base_datos():
    inspector = db.inspect(db.engine)
    columnas = [c['name'] for c in inspector.get_columns('vehiculo')]
    if 'preexistente' not in columnas:
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                'ALTER TABLE vehiculo ADD COLUMN preexistente BOOLEAN DEFAULT 0'
            )

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
        <div class="col-md-3">
            <div class="stat-card">
                <div><p>VEHÍCULOS ENTRADOS HOY</p><h2>{{ entrados_hoy }}</h2></div>
                <div class="stat-icon text-primary"><i class="bi bi-car-front-fill"></i></div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="stat-card">
                <div><p>OCUPACIÓN CELDAS</p><h2>{{ total_ocupados }} <span style="font-size: 1.1rem; color: var(--text-muted);">/ 60</span></h2></div>
                <div class="stat-icon text-warning"><i class="bi bi-shop"></i></div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="stat-card">
                <div><p>VENTAS TURNO ACTIVO</p><h2 style="color: var(--green);">${{ "{:,}".format(total_ventas).replace(',', '.') }}</h2></div>
                <div class="stat-icon text-success"><i class="bi bi-cash-register"></i></div>
            </div>
        </div>
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
                        <form action="/ingresar" method="POST">
                            <div class="mb-3">
                                <label class="form-label">Placa del Vehículo</label>
                                <input type="text" name="placa" class="form-control text-uppercase font-monospace fs-5 fw-bold" placeholder="EJ: ABC123" required autocomplete="off">
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
                            <div class="form-check mb-3">
                                <input class="form-check-input" type="checkbox" name="lavado" id="checkLavado">
                                <label class="form-check-label text-light" for="checkLavado">¿Incluye Servicio de Lavado?</label>
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
                        <h5 class="fw-bold mb-3"><i class="bi bi-car-front text-success me-2"></i> Vehículos Adentro ({{ activos|length }})</h5>
                        {% if activos %}
                            <div class="table-responsive">
                                <table class="table table-custom align-middle">
                                    <thead>
                                        <tr>
                                            <th>Placa</th>
                                            <th>Tipo</th>
                                            <th>Casilla</th>
                                            <th>Ingreso</th>
                                            <th>Lavado</th>
                                            <th>Cobro y Salida</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for v in activos %}
                                            <tr>
                                                <td class="fw-bold font-monospace fs-5 text-info">{{ v.placa }}</td>
                                                <td>{{ v.tipo }}</td>
                                                <td><span class="badge bg-secondary">Casilla E-{{ v.casilla }}</span></td>
                                                <td>{{ v.fecha_ingreso.strftime('%H:%M - %d/%m') }}</td>
                                                <td>{{ "Sí ($" ~ "{:,}".format(v.costo_lavado).replace(',', '.') ~ ")" if v.lavado else "No" }}</td>
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
    ahora = hora_colombia()
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

    activos = Vehiculo.query.filter_by(estado='activo').all()
    casillas = {v.casilla: v for v in activos}
    
    hoy_inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    entrados_hoy = Vehiculo.query.filter(Vehiculo.fecha_ingreso >= hoy_inicio).count()

    # VENTAS TURNO ACTIVO:
    # Solo se cuentan movimientos desde la última apertura de caja.
    # Si la caja está cerrada, el acumulado se muestra en $0.
    inicio_turno = caja.fecha_apertura if caja and caja.estado == 'abierta' else None

    if inicio_turno:
        vehiculos_cobrados = Vehiculo.query.filter(
            Vehiculo.estado == 'salida',
            Vehiculo.fecha_salida >= inicio_turno,
            Vehiculo.fecha_salida <= ahora
        ).all()

        ventas_vulc = VentaVulcanizadora.query.filter(
            VentaVulcanizadora.fecha >= inicio_turno,
            VentaVulcanizadora.fecha <= ahora
        ).all()

        lista_arriendos = Arriendo.query.filter(
            Arriendo.fecha >= inicio_turno,
            Arriendo.fecha <= ahora
        ).all()

        lista_gastos = Gasto.query.filter(
            Gasto.fecha >= inicio_turno,
            Gasto.fecha <= ahora
        ).all()
    else:
        vehiculos_cobrados = []
        ventas_vulc = []
        lista_arriendos = []
        lista_gastos = []

    total_ventas = (
        sum(v.total_pagado for v in vehiculos_cobrados)
        + sum(v.monto for v in ventas_vulc)
        + sum(a.monto for a in lista_arriendos)
    )
    total_gastos = sum(g.monto for g in lista_gastos)

    total_efectivo = (
        sum(v.total_pagado for v in vehiculos_cobrados if v.metodo_pago == 'Efectivo')
        + sum(v.monto for v in ventas_vulc if v.metodo_pago == 'Efectivo')
        + sum(a.monto for a in lista_arriendos if a.metodo_pago == 'Efectivo')
    )

    total_transferencia = (
        sum(v.total_pagado for v in vehiculos_cobrados if v.metodo_pago == 'Transferencia')
        + sum(v.monto for v in ventas_vulc if v.metodo_pago == 'Transferencia')
        + sum(a.monto for a in lista_arriendos if a.metodo_pago == 'Transferencia')
    )

    return render_template_string(HTML_TEMPLATE,
                                  ahora=ahora,
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
                                  lista_arriendos=lista_arriendos,
                                  lista_gastos=lista_gastos)

@app.route('/caja/abrir', methods=['POST'])
def abrir_caja():
    caja = Caja.query.first()
    caja.monto_inicial = limpiar_monto(request.form.get('monto_inicial'))
    caja.estado = 'abierta'
    caja.fecha_apertura = hora_colombia()
    db.session.commit()
    flash("Caja abierta correctamente.", "success")
    return redirect(url_for('index'))

@app.route('/caja/cerrar', methods=['POST'])
def cerrar_caja():
    caja = Caja.query.first()
    if caja:
        caja.estado = 'cerrada'
        db.session.commit()
    flash("Caja cerrada con éxito.", "success")
    return redirect(url_for('index'))

@app.route('/ingresar', methods=['POST'])
def ingresar():
    placa = request.form.get('placa').upper().strip()
    tipo = request.form.get('tipo')
    casilla = int(request.form.get('casilla'))
    lavado = True if request.form.get('lavado') else False
    costo_servicios = limpiar_monto(request.form.get('costo_servicios'))

    existente = Vehiculo.query.filter_by(casilla=casilla, estado='activo').first()
    if existente:
        flash(f"La casilla E-{casilla} ya se encuentra ocupada por el vehículo {existente.placa}.", "error")
        return redirect(url_for('index'))

    tarifa = Tarifa.query.filter_by(tipo=tipo).first()
    costo_lavado = tarifa.tarifa_lavado if lavado else 0

    nuevo = Vehiculo(
        placa=placa,
        tipo=tipo,
        casilla=casilla,
        lavado=lavado,
        costo_lavado=costo_lavado,
        costo_servicios=costo_servicios,
        fecha_ingreso=hora_colombia()
    )
    db.session.add(nuevo)
    db.session.commit()
    flash(f"Vehículo {placa} ingresado en casilla E-{casilla}.", "success")
    return redirect(url_for('index'))

@app.route('/salida/<int:id>', methods=['POST'])
def salida(id):
    vehiculo = Vehiculo.query.get_or_404(id)
    tipo_cobro = request.form.get('tipo_cobro')
    metodo_pago = request.form.get('metodo_pago')

    tarifa = Tarifa.query.filter_by(tipo=vehiculo.tipo).first()
    
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

    flash(f"Salida registrada para {vehiculo.placa}. Total cobrado: ${total:,}".replace(',', '.'), "success")
    return redirect(url_for('index'))

@app.route('/vulcanizadora/venta', methods=['POST'])
def vulcanizadora_venta():
    categoria = request.form.get('categoria')
    detalle_select = request.form.get('detalle_select')
    detalle_custom = request.form.get('detalle_custom')
    monto = limpiar_monto(request.form.get('monto'))
    metodo_pago = request.form.get('metodo_pago')

    servicio = detalle_custom if categoria == 'Otro' else f"{categoria} - {detalle_select}"

    venta = VentaVulcanizadora(servicio=servicio, monto=monto, metodo_pago=metodo_pago, fecha=hora_colombia())
    db.session.add(venta)
    db.session.commit()
    flash("Venta de vulcanizadora registrada.", "success")
    return redirect(url_for('index'))

@app.route('/arriendos/pagar', methods=['POST'])
def arriendo_pagar():
    local = request.form.get('local')
    inquilino = request.form.get('inquilino')
    monto = limpiar_monto(request.form.get('monto'))
    metodo_pago = request.form.get('metodo_pago')

    arriendo = Arriendo(local=local, inquilino=inquilino, monto=monto, metodo_pago=metodo_pago, fecha=hora_colombia())
    db.session.add(arriendo)
    db.session.commit()
    flash("Pago de arriendo registrado con éxito.", "success")
    return redirect(url_for('index'))

@app.route('/gasto', methods=['POST'])
def registrar_gasto():
    descripcion = request.form.get('descripcion')
    monto = limpiar_monto(request.form.get('monto'))

    gasto = Gasto(descripcion=descripcion, monto=monto, fecha=hora_colombia())
    db.session.add(gasto)
    db.session.commit()
    flash("Gasto registrado correctamente.", "success")
    return redirect(url_for('index'))

@app.route('/tarifas/actualizar/<int:id>', methods=['POST'])
def actualizar_tarifa(id):
    tarifa = Tarifa.query.get_or_404(id)
    tarifa.tarifa_hora = limpiar_monto(request.form.get('tarifa_hora'))
    tarifa.tarifa_lavado = limpiar_monto(request.form.get('tarifa_lavado'))
    tarifa.tarifa_mes = limpiar_monto(request.form.get('tarifa_mes'))
    db.session.commit()
    flash(f"Tarifas para {tarifa.nombre} actualizadas.", "success")
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        actualizar_base_datos()
    app.run(host='0.0.0.0', port=5000, debug=True)