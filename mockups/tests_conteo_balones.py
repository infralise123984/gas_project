"""Tests del conteo diario de balones (sección del bodeguero)."""

import re
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse

from mockups.models import (
    AuditoriaAccion,
    ConteoDiarioBalon,
    LineaConteoBalon,
    TipoBalon,
    Usuario,
)
from mockups.services.conteo import (
    es_editable,
    obtener_o_crear_conteo,
    saldos_iniciales,
)
from mockups.utils.fechas import today_chile


class BaseConteoTest(TestCase):
    def setUp(self):
        self.bodeguero = Usuario.objects.create_user(
            username='bode', password='Clave.Segura.123', rol='bodeguero'
        )
        self.camionero = Usuario.objects.create_user(
            username='cam', password='Clave.Segura.123', rol='camionero'
        )
        self.b15 = TipoBalon.objects.create(nombre='Gas 15 kg', peso_neto_gas=15)
        self.b5 = TipoBalon.objects.create(nombre='Gas 5 kg', peso_neto_gas=5)
        self.b_inactivo = TipoBalon.objects.create(
            nombre='Gas 45 kg', peso_neto_gas=45, activo=False
        )

    def _linea(self, conteo, balon):
        return conteo.lineas.get(balon=balon)


class ServicioConteoTest(BaseConteoTest):
    def test_crea_lineas_solo_de_balones_activos(self):
        conteo, creado = obtener_o_crear_conteo(today_chile(), self.bodeguero)

        self.assertTrue(creado)
        self.assertEqual(conteo.lineas.count(), 2)
        self.assertNotIn(
            self.b_inactivo,
            [linea.balon for linea in conteo.lineas.all()],
        )

    def test_no_duplica_el_conteo_del_mismo_dia(self):
        primero, creado_1 = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        segundo, creado_2 = obtener_o_crear_conteo(today_chile(), self.bodeguero)

        self.assertTrue(creado_1)
        self.assertFalse(creado_2)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(ConteoDiarioBalon.objects.count(), 1)

    def test_primer_conteo_arranca_en_cero(self):
        conteo, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)

        linea = self._linea(conteo, self.b15)
        self.assertEqual(linea.stock_inicial_llenos, 0)
        self.assertEqual(linea.stock_inicial_vacios, 0)

    def test_saldo_inicial_arrastra_del_dia_anterior(self):
        ayer = today_chile() - timedelta(days=1)
        conteo_ayer, _ = obtener_o_crear_conteo(ayer, self.bodeguero)

        linea_ayer = self._linea(conteo_ayer, self.b15)
        linea_ayer.llenos_entran = 10
        linea_ayer.llenos_salen = 4
        linea_ayer.vacios_entran = 7
        linea_ayer.vacios_salen = 2
        linea_ayer.save()

        conteo_hoy, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        linea_hoy = self._linea(conteo_hoy, self.b15)

        self.assertEqual(linea_hoy.stock_inicial_llenos, 6)
        self.assertEqual(linea_hoy.stock_inicial_vacios, 5)

    def test_saldos_iniciales_ignora_balones_no_presentes(self):
        ayer = today_chile() - timedelta(days=1)
        obtener_o_crear_conteo(ayer, self.bodeguero)

        saldos = saldos_iniciales(today_chile())

        self.assertIn(self.b15.id, saldos)
        self.assertEqual(saldos[self.b15.id], (0, 0))

    def test_stock_final_es_inicial_mas_entradas_menos_salidas(self):
        conteo, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        linea = self._linea(conteo, self.b15)

        linea.stock_inicial_llenos = 5
        linea.llenos_entran = 8
        linea.llenos_salen = 3
        linea.stock_inicial_vacios = 2
        linea.vacios_entran = 1
        linea.vacios_salen = 4

        self.assertEqual(linea.stock_final_llenos, 10)
        self.assertEqual(linea.stock_final_vacios, -1)

    def test_editable_solo_el_dia_correspondiente(self):
        conteo_hoy, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        ayer = today_chile() - timedelta(days=1)
        conteo_ayer, _ = obtener_o_crear_conteo(ayer, self.bodeguero)

        self.assertTrue(es_editable(conteo_hoy))
        self.assertFalse(es_editable(conteo_ayer))

    def test_descuadre_cuando_el_saldo_final_es_negativo(self):
        conteo, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        linea = self._linea(conteo, self.b15)

        self.assertFalse(linea.tiene_descuadre)

        linea.llenos_salen = 1
        self.assertTrue(linea.tiene_descuadre)


class VistasConteoTest(BaseConteoTest):
    def test_anonimo_va_al_login(self):
        respuesta = self.client.get(reverse('conteo_balones_lista'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/auth/login/', respuesta['Location'])

    def test_bodeguero_puede_ver_la_lista(self):
        self.client.force_login(self.bodeguero)

        respuesta = self.client.get(reverse('conteo_balones_lista'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'balones/conteo_lista.html')

    def test_camionero_es_rechazado_y_auditado(self):
        self.client.force_login(self.camionero)

        respuesta = self.client.get(reverse('conteo_balones_lista'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(
            AuditoriaAccion.objects.filter(tipo='PERM_DENIED', username='cam').exists()
        )

    def test_conteo_hoy_crea_y_redirige_al_detalle(self):
        self.client.force_login(self.bodeguero)

        respuesta = self.client.get(reverse('conteo_balones_hoy'))

        conteo = ConteoDiarioBalon.objects.get(fecha=today_chile())
        self.assertRedirects(
            respuesta, reverse('conteo_balones_detalle', args=[conteo.id])
        )
        self.assertTrue(AuditoriaAccion.objects.filter(tipo='CONTEO_CREATE').exists())

    def test_conteo_hoy_no_falla_si_ya_existe(self):
        conteo, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        self.client.force_login(self.bodeguero)

        respuesta = self.client.get(reverse('conteo_balones_hoy'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(ConteoDiarioBalon.objects.count(), 1)

    def test_post_guarda_los_movimientos(self):
        conteo, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        lineas = list(conteo.lineas.order_by('id'))
        self.client.force_login(self.bodeguero)

        # El prefijo del formset lo deriva Django del related_name ('lineas').
        datos = {
            'lineas-TOTAL_FORMS': str(len(lineas)),
            'lineas-INITIAL_FORMS': str(len(lineas)),
            'lineas-MIN_NUM_FORMS': '0',
            'lineas-MAX_NUM_FORMS': '1000',
        }
        for indice, linea in enumerate(lineas):
            datos[f'lineas-{indice}-id'] = str(linea.id)
            datos[f'lineas-{indice}-llenos_entran'] = '6'
            datos[f'lineas-{indice}-llenos_salen'] = '1'
            datos[f'lineas-{indice}-vacios_entran'] = '3'
            datos[f'lineas-{indice}-vacios_salen'] = '2'

        respuesta = self.client.post(
            reverse('conteo_balones_detalle', args=[conteo.id]), datos
        )

        self.assertEqual(respuesta.status_code, 302)
        linea = conteo.lineas.get(pk=lineas[0].id)
        self.assertEqual(linea.llenos_entran, 6)
        self.assertEqual(linea.llenos_salen, 1)
        self.assertEqual(linea.stock_final_llenos, 5)
        self.assertEqual(linea.stock_final_vacios, 1)
        self.assertTrue(AuditoriaAccion.objects.filter(tipo='CONTEO_UPDATE').exists())

    def test_post_en_conteo_de_ayer_es_rechazado(self):
        ayer = today_chile() - timedelta(days=1)
        conteo, _ = obtener_o_crear_conteo(ayer, self.bodeguero)
        linea = conteo.lineas.first()
        self.client.force_login(self.bodeguero)

        datos = {
            'lineas-TOTAL_FORMS': '1',
            'lineas-INITIAL_FORMS': '1',
            'lineas-MIN_NUM_FORMS': '0',
            'lineas-MAX_NUM_FORMS': '1000',
            'lineas-0-id': str(linea.id),
            'lineas-0-llenos_entran': '99',
            'lineas-0-llenos_salen': '0',
            'lineas-0-vacios_entran': '0',
            'lineas-0-vacios_salen': '0',
        }

        respuesta = self.client.post(
            reverse('conteo_balones_detalle', args=[conteo.id]), datos
        )

        self.assertEqual(respuesta.status_code, 302)
        linea.refresh_from_db()
        self.assertEqual(linea.llenos_entran, 0)
        self.assertFalse(AuditoriaAccion.objects.filter(tipo='CONTEO_UPDATE').exists())

    def test_detalle_de_ayer_no_muestra_el_boton_de_guardar(self):
        ayer = today_chile() - timedelta(days=1)
        conteo, _ = obtener_o_crear_conteo(ayer, self.bodeguero)
        self.client.force_login(self.bodeguero)

        respuesta = self.client.get(
            reverse('conteo_balones_detalle', args=[conteo.id])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context['editable'])

    def test_detalle_de_hoy_renderiza_la_tabla_editable(self):
        conteo, _ = obtener_o_crear_conteo(today_chile(), self.bodeguero)
        self.client.force_login(self.bodeguero)

        respuesta = self.client.get(
            reverse('conteo_balones_detalle', args=[conteo.id])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context['editable'])

        contenido = respuesta.content.decode()
        self.assertIn('conteo-tabla', contenido)
        self.assertIn('fila-conteo', contenido)

        # Cuatro movimientos por balón: llenos/vacíos x entran/salen.
        # Se cuenta por el atributo name (el hidden del id no coincide con el patrón).
        movimientos = re.findall(
            r'name="lineas-\d+-(?:llenos_entran|llenos_salen|vacios_entran|vacios_salen)"',
            contenido,
        )
        self.assertEqual(len(movimientos), conteo.lineas.count() * 4)
