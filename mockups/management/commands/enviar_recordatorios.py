"""
Management command para enviar recordatorios de pedidos pendientes.
Diseñado para ejecutarse via cron o tarea programada.

Uso:
    python manage.py enviar_recordatorios

Ejemplo de cron (cada 30 minutos):
    */30 * * * * cd /ruta/proyecto && python manage.py enviar_recordatorios
"""
from django.core.management.base import BaseCommand
from mockups.push_notifications import notificar_recordatorio_pendientes


class Command(BaseCommand):
    help = 'Envía recordatorios push a camioneros sobre pedidos pendientes'
    
    def handle(self, *args, **options):
        self.stdout.write('Enviando recordatorios de pedidos pendientes...')
        
        resultado = notificar_recordatorio_pendientes()
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Completado: {resultado['enviados']} notificaciones enviadas "
                f"para {resultado['pedidos']} pedidos pendientes"
            )
        )
