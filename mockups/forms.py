# mockups/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory, BaseInlineFormSet
from django.utils.safestring import mark_safe
from .models import Pedido, DetallePedido, TipoBalon, SobreDiario, LineaSobre, LineaPago, LineaGasto, Usuario

# Sectores definidos en Pedido.SECTORES (fuente única de verdad)
SECTORES = [("", "— Seleccionar sector —")] + Pedido.SECTORES

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
        if value and value in self.prices_dict:
            option['attrs']['data-precio'] = str(self.prices_dict[value])
        return option


class DetallePedidoForm(forms.ModelForm):
    class Meta:
        model = DetallePedido
        fields = ['balon', 'cantidad', 'precio_venta_unitario']
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
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

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
        choices=SECTORES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False,
        label="Sector / Población",
    )
    
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
        # Aquí podrías agregar validaciones extras si quieres (ej: suma mínima de declaradas)


LineaSobreFormSet = inlineformset_factory(
    SobreDiario,
    LineaSobre,
    form=LineaSobreForm,
    formset=BaseLineaSobreFormSet,
    extra=0,                # No se agregan líneas nuevas manualmente (ya están creadas por balones)
    can_delete=False,       # No se eliminan balones del sobre
    fields=('cantidad_declarada', 'nota'),
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