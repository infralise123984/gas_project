from django.shortcuts import render

def sas(request):
    return render(request, 'index.html')


def login(request):
    return render(request, 'login.html')

def mantenedor_clientes(request):
    # Datos falsos para el mockup
    clientes = [
        {'id': 1, 'nombre': 'Juan Pérez', 'telefono': '987654321', 'direccion': 'Av. Siempre Viva 123'},
        {'id': 2, 'nombre': 'María López', 'telefono': '912345678', 'direccion': 'Calle Falsa 456'},
    ]
    return render(request, 'mantenedor_clientes.html', {'clientes': clientes})

def consultas_pedidos(request):
    pedidos = [
        {'id': 101, 'cliente': 'Juan Pérez', 'balon': '10kg', 'fecha': '2025-04-01', 'estado': 'Entregado'},
        {'id': 102, 'cliente': 'María López', 'balon': '45kg', 'fecha': '2025-04-02', 'estado': 'En ruta'},
    ]
    return render(request, 'consultas_pedidos.html', {'pedidos': pedidos})

def transaccional_pedido(request):
    return render(request, 'transaccional_pedido.html')

def reporte_ventas(request):
    return render(request, 'reporte_ventas.html')

def cliente_pedido(request):
    return render(request, 'cliente_pedido.html')


def camionero_entregas(request):
    # Simulamos que el camionero TIENE una entrega activa
    entrega_activa = {
        'id': 1042,
        'cliente': 'Juan Pérez',
        'direccion': 'Av. Los Pinos 123, San Miguel',
        'balon': '10 kg',
        'metodo_pago': 'Efectivo',
        'monto': '45.00',
        'distancia': '800 m',
        'tiempo_estimado': '3 min',
        'estado': 'En ruta'
    }

    # Si quisieras simular que NO tiene entrega, usarías:
    # entrega_activa = None

    return render(request, 'camionero_entregas.html', {
        'entrega_activa': entrega_activa,
        'camion_id': 7  # identificador del camión
    })