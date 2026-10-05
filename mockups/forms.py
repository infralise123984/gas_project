# mockups/forms.py
from decimal import Decimal, ROUND_FLOOR

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory, BaseInlineFormSet
from django.utils.safestring import mark_safe
from .models import (
    ConteoDiarioBalon,
    DetallePedido,
    LineaConteoBalon,
    LineaGasto,
    LineaPago,
    LineaSobre,
    Pedido,
    Sector,
    SobreDiario,
    TipoBalon,
    Usuario,
)
from .utils.permisos import ROL_PROPIETARIO_DESCUENTO, ROLES_CON_DESCUENTO


def get_sector_choices(include_blank=True, include_inactive=False, selected_value=None):
    """Retorna opciones de sector priorizando el catálogo administrable y usando fallback legacy."""
    queryset = Sector.objects.all()
    if not include_inactive:
        queryset = queryset.filter(activo=True)

    sectores = list(queryset.order_by('zona', 'nombre'))
    if sectores:
        grouped_choices = []
        for zona_value, zona_label in Sector.ZONAS:
            opciones_zona = [
                (sector.nombre, sector.nombre)
                for sector in sectores
                if sector.zona == zona_value
            ]
            if opciones_zona:
                grouped_choices.append((zona_label, opciones_zona))
        choices = grouped_choices
    else:
        choices = list(Pedido.SECTORES)

    available_values = set()
    for item in choices:
        if isinstance(item[1], (list, tuple)):
            for value, _label in item[1]:
                available_values.add(value)
        else:
            available_values.add(item[0])

    if selected_value and selected_value not in available_values:
        choices.append((selected_value, f"[LEGACY] {selected_value}"))

    if include_blank:
        return [("", "— Seleccionar sector —")] + choices
    return choices

User = get_user_model()


class CrearUsuarioSeguroForm(forms.Form):
    username = forms.CharField(
        label="Nombre de usuario",
        max_length=150,
        validators=[UnicodeUsernameValidator()],
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Ej: juan.perez',
            'maxlength': '150',
            'autocomplete': 'off',
        }),
    )
    telefono = forms.CharField(
        label="Teléfono",
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Ej: +56987654321',
            'maxlength': '15',
            'inputmode': 'tel',
            'autocomplete': 'off',
        }),
    )
    first_name = forms.CharField(
        label="Nombre",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Ej: Juan',
            'maxlength': '150',
            'autocomplete': 'off',
        }),
    )
    last_name = forms.CharField(
        label="Apellido",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Ej: Pérez',
            'maxlength': '150',
            'autocomplete': 'off',
        }),
    )
    rol = forms.ChoiceField(
        label="Rol",
        choices=[("", "— Seleccionar rol —")] + Usuario.ROLES,
        widget=forms.Select(attrs={
            'class': 'form-select form-select-lg',
        }),
    )
    password1 = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'autocomplete': 'new-password',
        }),
    )
    password2 = forms.CharField(
        label="Repetir contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'autocomplete': 'new-password',
        }),
    )
    admin_password = forms.CharField(
        label="Tu contraseña actual",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'autocomplete': 'current-password',
        }),
        help_text="Confirma tu propia contraseña para autorizar la creación del usuario.",
    )

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ya existe un usuario con ese nombre de usuario.")
        return username

    def clean_telefono(self):
        telefono = (self.cleaned_data.get('telefono') or '').strip()
        if not telefono:
            return ''
        if not telefono.replace('+', '', 1).isdigit():
            raise forms.ValidationError("Ingresa un teléfono válido usando solo números y un '+' opcional al inicio.")
        if not 8 <= len(telefono.replace('+', '', 1)) <= 15:
            raise forms.ValidationError("El teléfono debe tener entre 8 y 15 dígitos.")
        if User.objects.filter(telefono=telefono).exists():
            raise forms.ValidationError("Ya existe un usuario con ese teléfono.")
        return telefono

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        admin_password = cleaned_data.get('admin_password')

        if password1 and password2 and password1 != password2:
            self.add_error('password2', "Las contraseñas no coinciden.")

        if self.request_user and admin_password and not self.request_user.check_password(admin_password):
            self.add_error('admin_password', "La contraseña actual no es correcta.")

        if password1:
            candidate_user = User(
                username=cleaned_data.get('username', ''),
                first_name=cleaned_data.get('first_name', ''),
                last_name=cleaned_data.get('last_name', ''),
            )
            try:
                validate_password(password1, user=candidate_user)
            except ValidationError as exc:
                self.add_error('password1', exc)

        return cleaned_data


class BalonSelectWidget(forms.Select):
    """Widget personalizado que agrega data-precio a cada opción"""
    def __init__(self, attrs=None, prices_dict=None):
        super().__init__(attrs)
        self.prices_dict = prices_dict or {}
    
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        """Agregar data-precio a cada opción"""
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        raw_value = getattr(value, 'value', value)
        normalized_value = '' if raw_value in (None, '') else str(raw_value)

        if normalized_value and normalized_value in self.prices_dict:
            option['attrs']['data-precio'] = str(self.prices_dict[normalized_value])
        return option


# Tope de descuento por unidad, como fracción del precio de venta del balón.
TOPE_DESCUENTO_UNITARIO = Decimal('0.5')


class DetallePedidoForm(forms.ModelForm):
    class Meta:
        model = DetallePedido
        fields = ['balon', 'cantidad', 'precio_venta_unitario', 'descuento_unitario']
        widgets = {
            'balon': forms.Select(attrs={'class': 'form-select balon-select'}),
            'cantidad': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'value': 1,
                'style': 'width: 100px;',
            }),
            'precio_venta_unitario': forms.NumberInput(attrs={
                'class': 'form-control precio-display',
                'readonly': True,
                'style': 'width: 120px; background-color: #f0f0f0;',
            }),
            'descuento_unitario': forms.NumberInput(attrs={
                'class': 'form-control descuento-unitario-input',
                'min': 0,
                'step': 1,
                'placeholder': '0',
                'inputmode': 'numeric',
            }),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        self.rol = getattr(user, 'rol', None)
        self._configurar_campo_descuento()

        # Choices para balones ordenados como en los sobres: Normal→Catalítico→Aluminio, cada uno por peso desc
        from django.db.models import Case, When, Value, IntegerField
        balones = TipoBalon.objects.filter(activo=True).annotate(
            tipo_orden=Case(
                When(tipo_gas='normal', then=Value(0)),
                When(tipo_gas='catalitico', then=Value(1)),
                When(tipo_gas='aluminio', then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by('tipo_orden', '-peso_neto_gas')
        
        choices = [('', '— Seleccionar balón —')]
        prices_dict = {}
        
        for b in balones:
            if user and user.rol == 'bodeguero':
                precio = b.precio_local
            else:
                precio = b.precio_domicilio
            
            precio_txt = f"${int(precio):,}".replace(',', '.')
            tipo_gas_display = b.get_tipo_gas_display()
            choices.append((b.id, f"{b.nombre} - {tipo_gas_display} - {precio_txt}"))
            prices_dict[str(b.id)] = int(precio)
        
        # Usar el widget personalizado con precios
        self.fields['balon'].widget = BalonSelectWidget(
            attrs={'class': 'form-select balon-select'},
            prices_dict=prices_dict
        )
        self.fields['balon'].choices = choices
        self.fields['cantidad'].initial = 1
        
        # No hacer required en el form, lo validaremos en el formset
        self.fields['balon'].required = False
        self.fields['cantidad'].required = False
        self.fields['precio_venta_unitario'].required = False

    def _configurar_campo_descuento(self):
        """Oculta el descuento a quien no puede otorgarlo y lo congela si ya fue aplicado.

        - Bodeguero y camionero: no ven el campo (no otorgan ni modifican descuentos).
        - Jefe y admin: pueden otorgar un descuento nuevo, pero solo el telefonista
          puede modificar uno ya aplicado.
        """
        if 'descuento_unitario' not in self.fields:
            return

        if self.rol not in ROLES_CON_DESCUENTO:
            del self.fields['descuento_unitario']
            return

        if self.rol != ROL_PROPIETARIO_DESCUENTO and getattr(self.instance, 'descuento_unitario', 0):
            self.fields['descuento_unitario'].disabled = True

    def _precio_venta_base(self, balon):
        """Precio de venta que la vista persistirá para el balón, según el rol del usuario."""
        if self.rol == 'bodeguero':
            return balon.precio_local
        return balon.precio_domicilio

    def clean(self):
        """Valida que el descuento por unidad no supere el tope sobre el precio del balón."""
        cleaned_data = super().clean()

        descuento = cleaned_data.get('descuento_unitario')
        balon = cleaned_data.get('balon')
        if not descuento or not balon:
            return cleaned_data

        precio_base = self._precio_venta_base(balon)
        if not precio_base:
            return cleaned_data

        tope = (Decimal(precio_base) * TOPE_DESCUENTO_UNITARIO).to_integral_value(rounding=ROUND_FLOOR)
        if Decimal(descuento) > tope:
            self.add_error(
                'descuento_unitario',
                (
                    f"El descuento por balón no puede superar el 50% de su precio "
                    f"({balon.nombre}). Máximo por balón: ${int(tope):,}."
                ).replace(',', '.'),
            )
        return cleaned_data


class BaseDetalleFormSet(BaseInlineFormSet):
    def clean(self):
        """Validación global: al menos un producto válido y sin tipos de balón repetidos."""
        super().clean()
        
        if any(self.errors):
            return
            
        valid_count = 0
        seen_balones = {}  # balon_id -> nombre, para detectar duplicados
        for form in self.forms:
            # Ignorar formularios vacíos o marcados para eliminación
            if not form.cleaned_data or form.cleaned_data.get('DELETE', False):
                continue
                
            balon = form.cleaned_data.get('balon')
            cantidad = form.cleaned_data.get('cantidad')
            
            # Contar solo si tiene ambos campos válidos
            if balon and cantidad and cantidad > 0:
                if balon.id in seen_balones:
                    raise forms.ValidationError(
                        f"El balón '{balon.nombre}' está repetido. "
                        "Usa una sola fila y ajusta la cantidad."
                    )
                seen_balones[balon.id] = balon.nombre
                valid_count += 1
        
        if valid_count < 1:
            raise forms.ValidationError("Debe agregar al menos un producto con balón y cantidad válidos.")


# Formset para pedidos - ajustado para permitir formularios vacíos inicialmente
DetalleFormSet = inlineformset_factory(
    Pedido,
    DetallePedido,
    form=DetallePedidoForm,
    formset=BaseDetalleFormSet,
    extra=1,  # Mostrar 1 formulario vacío inicialmente
    can_delete=True,
    min_num=0,
    validate_min=False,
)


class PedidoCabeceraForm(forms.ModelForm):
    """Formulario solo para los campos de cabecera del pedido"""
    sector = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False,
        label="Sector / Población",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        valor_actual = None
        if self.is_bound:
            valor_actual = (self.data.get(self.add_prefix('sector')) or '').strip()
        elif self.instance and self.instance.pk:
            valor_actual = (self.instance.sector or '').strip()

        self.fields['sector'].choices = get_sector_choices(selected_value=valor_actual)
    
    class Meta:
        model = Pedido
        fields = ['metodo_pago', 'sector', 'direccion_entrega']
        widgets = {
            'metodo_pago': forms.Select(attrs={'class': 'form-select'}),
            'direccion_entrega': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Calle, número, casa esquina, depto, referencia clara...'
            }),
        }


# ──────────────────────────────────────────────────────────────
# FORMULARIOS Y FORMSETS PARA SOBRES DIARIOS
# ──────────────────────────────────────────────────────────────

class LineaSobreForm(forms.ModelForm):
    class Meta:
        model = LineaSobre
        fields = ['cantidad_declarada', 'nota']
        widgets = {
            'cantidad_declarada': forms.NumberInput(attrs={
                'class': 'form-control fs-4 text-center fw-bold',
                'min': 0,
                'style': 'width: 120px;'
            }),
            'nota': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Ej: Faltó registrar 2 balones de 15 kg en la app'
            }),
        }


class BaseLineaSobreFormSet(BaseInlineFormSet):
    def get_queryset(self):
        """Ordena las líneas del sobre: primero normales por peso desc, luego catalíticos, etc."""
        from django.db.models import Case, When, Value, IntegerField
        qs = super().get_queryset()
        return qs.annotate(
            tipo_orden=Case(
                When(balon__tipo_gas='normal', then=Value(0)),
                When(balon__tipo_gas='catalitico', then=Value(1)),
                When(balon__tipo_gas='aluminio', then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by('tipo_orden', '-balon__peso_neto_gas')
    
    def clean(self):
        super().clean()
        if any(self.errors):
            return


LineaSobreFormSet = inlineformset_factory(
    SobreDiario,
    LineaSobre,
    form=LineaSobreForm,
    formset=BaseLineaSobreFormSet,
    extra=0,                # No se agregan líneas nuevas manualmente (ya están creadas por balones)
    can_delete=False,       # No se eliminan balones del sobre
    fields=('cantidad_declarada', 'nota'),
)


# ──────────────────────────────────────────────────────────────
# FORMULARIO Y FORMSET PARA EL CONTEO DIARIO DE BALONES
# ──────────────────────────────────────────────────────────────

CAMPOS_MOVIMIENTO_CONTEO = ('llenos_entran', 'llenos_salen', 'vacios_entran', 'vacios_salen')


class LineaConteoBalonForm(forms.ModelForm):
    """Movimientos del día para un tipo de balón.

    Los saldos (inicial y final) no son editables aquí: el inicial lo arrastra
    el servicio desde el conteo anterior y el final se calcula.
    """

    class Meta:
        model = LineaConteoBalon
        fields = CAMPOS_MOVIMIENTO_CONTEO
        widgets = {
            campo: forms.NumberInput(attrs={
                'class': 'form-control form-control-lg text-center fw-bold',
                'min': 0,
                'inputmode': 'numeric',
                'autocomplete': 'off',
            })
            for campo in CAMPOS_MOVIMIENTO_CONTEO
        }


class BaseLineaConteoBalonFormSet(BaseInlineFormSet):
    def get_queryset(self):
        """Mismo orden que los sobres: clásicos, catalíticos y aluminio; peso desc."""
        from django.db.models import Case, When, Value, IntegerField
        qs = super().get_queryset()
        return qs.annotate(
            tipo_orden=Case(
                When(balon__tipo_gas='normal', then=Value(0)),
                When(balon__tipo_gas='catalitico', then=Value(1)),
                When(balon__tipo_gas='aluminio', then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by('tipo_orden', '-balon__peso_neto_gas')


LineaConteoBalonFormSet = inlineformset_factory(
    ConteoDiarioBalon,
    LineaConteoBalon,
    form=LineaConteoBalonForm,
    formset=BaseLineaConteoBalonFormSet,
    extra=0,            # Las líneas ya existen: una por tipo de balón activo
    can_delete=False,   # Un tipo de balón no se saca del conteo del día
    fields=CAMPOS_MOVIMIENTO_CONTEO,
)


DetalleFormSetEdit = inlineformset_factory(
    Pedido,
    DetallePedido,
    form=DetallePedidoForm,
    formset=BaseDetalleFormSet,
    extra=0,
    can_delete=True,
    min_num=0,
    validate_min=False,
)


class AdminDescuentoLineaForm(forms.ModelForm):
    """Corrección administrativa: edita SOLO el descuento por balón de una línea.

    A diferencia de `DetallePedidoForm`, no expone balón ni cantidad: la corrección
    apunta únicamente a la rebaja olvidada. El tope (50%) se valida contra el precio
    de venta YA registrado en la línea (`precio_venta_unitario`), no contra el precio
    vigente del balón: la venta es un snapshot y la corrección no debe depender de
    cambios de precio posteriores.
    """

    class Meta:
        model = DetallePedido
        fields = ['descuento_unitario']
        widgets = {
            'descuento_unitario': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0,
                'step': 1,
                'placeholder': '0',
                'inputmode': 'numeric',
            }),
        }

    def clean_descuento_unitario(self):
        descuento = self.cleaned_data.get('descuento_unitario') or Decimal(0)
        precio_base = self.instance.precio_venta_unitario

        if descuento and precio_base:
            tope = (Decimal(precio_base) * TOPE_DESCUENTO_UNITARIO).to_integral_value(
                rounding=ROUND_FLOOR
            )
            if Decimal(descuento) > tope:
                self.add_error(
                    'descuento_unitario',
                    (
                        f"El descuento por balón no puede superar el 50% de su precio "
                        f"({self.instance.balon.nombre}). Máximo por balón: ${int(tope):,}."
                    ).replace(',', '.'),
                )
        return descuento


AdminDescuentoFormSet = inlineformset_factory(
    Pedido,
    DetallePedido,
    form=AdminDescuentoLineaForm,
    extra=0,          # Las líneas existen: solo se corrige su descuento
    can_delete=False,  # Esta corrección no agrega ni quita balones
    min_num=0,
    validate_min=False,
)


class LineaPagoForm(forms.ModelForm):
    class Meta:
        model = LineaPago
        fields = ['tipo_pago', 'monto', 'referencia']
        widgets = {
            'tipo_pago': forms.Select(attrs={'class': 'form-select'}),
            'monto': forms.NumberInput(attrs={
                'class': 'form-control text-end',
                'min': 0,
                'step': 1,
                'placeholder': '0'
            }),
            'referencia': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: Nº transacción o cheque'
            }),
        }


LineaPagoFormSet = inlineformset_factory(
    SobreDiario,
    LineaPago,
    form=LineaPagoForm,
    extra=0,                # Permite agregar líneas nuevas
    can_delete=True,
    min_num=0,
)


class LineaGastoForm(forms.ModelForm):
    class Meta:
        model = LineaGasto
        fields = ['descripcion', 'monto', 'nota']
        widgets = {
            'descripcion': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: Compra agua, pago aseo, combustible'
            }),
            'monto': forms.NumberInput(attrs={
                'class': 'form-control text-end',
                'min': 0,
                'step': 1,
                'placeholder': '0'
            }),
            'nota': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Detalles adicionales (opcional)'
            }),
        }


LineaGastoFormSet = inlineformset_factory(
    SobreDiario,
    LineaGasto,
    form=LineaGastoForm,
    extra=0,                # Permite agregar líneas nuevas
    can_delete=True,
    min_num=0,
)


# ──────────────────────────────────────────────────────────────
# FORMULARIOS PARA GESTIÓN DE BALONES (desde la web, sin admin)
# ──────────────────────────────────────────────────────────────

class TipoBalonForm(forms.ModelForm):
    """Formulario para crear/editar tipos de balones desde la web (sin precios)"""
    class Meta:
        model = TipoBalon
        fields = ['nombre', 'peso_neto_gas', 'tipo_gas', 'activo']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: Gas 5 kg, Gas 15 kg, Gas 45 kg',
                'maxlength': 50,
            }),
            'peso_neto_gas': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'placeholder': 'Ej: 5, 11, 15, 45',
            }),
            'tipo_gas': forms.Select(attrs={
                'class': 'form-select',
            }),
            'activo': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
                'style': 'width: 1.5rem; height: 1.5rem;',
            }),
        }
        labels = {
            'nombre': 'Nombre comercial',
            'peso_neto_gas': 'Peso neto de gas (kg)',
            'tipo_gas': 'Tipo de gas',
            'activo': 'Disponible para venta',
        }

    def clean(self):
        cleaned_data = super().clean()
        nombre = cleaned_data.get('nombre', '').strip()
        peso = cleaned_data.get('peso_neto_gas')
        
        # Validar que nombre no esté vacío
        if not nombre:
            self.add_error('nombre', 'El nombre no puede estar vacío')
        
        # Validar que peso sea positivo
        if peso and peso <= 0:
            self.add_error('peso_neto_gas', 'El peso debe ser mayor a 0')
        
        return cleaned_data


class EditarPerfilForm(forms.Form):
    first_name = forms.CharField(
        label="Nombre",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: Juan',
            'autocomplete': 'given-name',
        }),
    )
    last_name = forms.CharField(
        label="Apellido",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: Pérez',
            'autocomplete': 'family-name',
        }),
    )
    email = forms.EmailField(
        label="Correo electrónico",
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: juan@ejemplo.com',
            'autocomplete': 'email',
        }),
    )
    telefono = forms.CharField(
        label="Teléfono",
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: +56987654321',
            'inputmode': 'tel',
            'autocomplete': 'tel',
        }),
    )

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean_telefono(self):
        telefono = (self.cleaned_data.get('telefono') or '').strip()
        if not telefono:
            return ''
        if not telefono.replace('+', '', 1).isdigit():
            raise forms.ValidationError("Ingresa un teléfono válido usando solo números y un '+' opcional al inicio.")
        if not 8 <= len(telefono.replace('+', '', 1)) <= 15:
            raise forms.ValidationError("El teléfono debe tener entre 8 y 15 dígitos.")
        qs = User.objects.filter(telefono=telefono)
        if self.current_user:
            qs = qs.exclude(pk=self.current_user.pk)
        if qs.exists():
            raise forms.ValidationError("Ya existe un usuario con ese teléfono.")
        return telefono

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        qs = User.objects.filter(email__iexact=email)
        if self.current_user:
            qs = qs.exclude(pk=self.current_user.pk)
        if qs.exists():
            raise forms.ValidationError("Ya existe un usuario con ese correo electrónico.")
        return email


class CambiarPasswordForm(forms.Form):
    password_actual = forms.CharField(
        label="Contraseña actual",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'current-password',
        }),
    )
    password_nueva = forms.CharField(
        label="Nueva contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )
    password_confirmar = forms.CharField(
        label="Confirmar nueva contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
    )

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean_password_actual(self):
        password_actual = self.cleaned_data.get('password_actual')
        if self.current_user and not self.current_user.check_password(password_actual):
            raise forms.ValidationError("La contraseña actual no es correcta.")
        return password_actual

    def clean(self):
        cleaned_data = super().clean()
        nueva = cleaned_data.get('password_nueva')
        confirmar = cleaned_data.get('password_confirmar')

        if nueva and confirmar and nueva != confirmar:
            self.add_error('password_confirmar', "Las contraseñas nuevas no coinciden.")

        if nueva and self.current_user:
            try:
                validate_password(nueva, user=self.current_user)
            except ValidationError as exc:
                self.add_error('password_nueva', exc)

        return cleaned_data


class Verificar2FAForm(forms.Form):
    codigo = forms.CharField(
        label="Código de verificación",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-center',
            'placeholder': '000000',
            'inputmode': 'numeric',
            'autocomplete': 'one-time-code',
            'autofocus': True,
            'maxlength': '6',
            'pattern': '[0-9]{6}',
        }),
    )

    def clean_codigo(self):
        codigo = (self.cleaned_data.get('codigo') or '').strip()
        if not codigo.isdigit():
            raise forms.ValidationError("El código debe contener solo dígitos.")
        return codigo


class Activar2FAConfirmForm(forms.Form):
    codigo = forms.CharField(
        label="Código de confirmación",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-center',
            'placeholder': '000000',
            'inputmode': 'numeric',
            'autocomplete': 'one-time-code',
            'autofocus': True,
            'maxlength': '6',
            'pattern': '[0-9]{6}',
        }),
        help_text="Ingresa el código de 6 dígitos que aparece en tu aplicación autenticadora.",
    )

    def clean_codigo(self):
        codigo = (self.cleaned_data.get('codigo') or '').strip()
        if not codigo.isdigit():
            raise forms.ValidationError("El código debe contener solo dígitos.")
        return codigo


class Desactivar2FAForm(forms.Form):
    password_actual = forms.CharField(
        label="Contraseña actual",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'current-password',
        }),
        help_text="Confirma tu contraseña para desactivar la verificación en dos pasos.",
    )

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean_password_actual(self):
        password = self.cleaned_data.get('password_actual')
        if self.current_user and not self.current_user.check_password(password):
            raise forms.ValidationError("La contraseña no es correcta.")
        return password


class SectorForm(forms.ModelForm):
    class Meta:
        model = Sector
        fields = ['nombre', 'zona', 'activo']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: Población José Olivares',
                'maxlength': 100,
            }),
            'zona': forms.Select(attrs={
                'class': 'form-select',
            }),
            'activo': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
                'style': 'width: 1.5rem; height: 1.5rem;',
            }),
        }
        labels = {
            'nombre': 'Nombre del sector',
            'zona': 'Zona',
            'activo': 'Disponible para uso futuro',
        }

    def clean_nombre(self):
        nombre = (self.cleaned_data.get('nombre') or '').strip()
        if not nombre:
            raise forms.ValidationError('Debes ingresar un nombre de sector.')

        queryset = Sector.objects.filter(nombre__iexact=nombre)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError('Ya existe un sector con ese nombre.')
        return nombre
