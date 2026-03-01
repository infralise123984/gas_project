# mockups/forms.py
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import Pedido, DetallePedido, TipoBalon, SobreDiario, LineaSobre, LineaPago, LineaGasto

# Sectores definidos en Pedido.SECTORES (fuente única de verdad)
SECTORES = [("", "— Seleccionar sector —")] + Pedido.SECTORES


class DetallePedidoForm(forms.ModelForm):
    class Meta:
        model = DetallePedido
        fields = ['balon', 'cantidad']
        widgets = {
            'balon': forms.Select(attrs={'class': 'form-select'}),
            'cantidad': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'value': 1,
                'style': 'width: 100px;',
            }),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Choices para balones
        balones = TipoBalon.objects.filter(activo=True).order_by('peso_neto_gas')
        choices = [('', '— Seleccionar balón —')]
        for b in balones:
            if user and user.rol == 'bodeguero':
                precio = b.precio_local
            else:
                precio = b.precio_domicilio
            precio_txt = f"${int(precio):,}".replace(',', '.')
            choices.append((b.id, f"{b.nombre} - {precio_txt}"))
        self.fields['balon'].choices = choices
        self.fields['cantidad'].initial = 1
        
        # No hacer required en el form, lo validaremos en el formset
        self.fields['balon'].required = False
        self.fields['cantidad'].required = False


class BaseDetalleFormSet(BaseInlineFormSet):
    def clean(self):
        """Validación global: al menos un producto válido"""
        super().clean()
        
        if any(self.errors):
            return
            
        valid_count = 0
        for form in self.forms:
            # Ignorar formularios vacíos o marcados para eliminación
            if form.cleaned_data.get('DELETE', False):
                continue
                
            balon = form.cleaned_data.get('balon')
            cantidad = form.cleaned_data.get('cantidad')
            
            # Contar solo si tiene ambos campos válidos
            if balon and cantidad and cantidad > 0:
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
    """Formulario para crear/editar tipos de balones desde la web"""
    class Meta:
        model = TipoBalon
        fields = ['nombre', 'peso_neto_gas', 'tipo_gas', 'precio_compra', 'precio_local', 'precio_domicilio', 'activo']
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
            'precio_compra': forms.NumberInput(attrs={
                'class': 'form-control text-end',
                'min': 0,
                'step': 1,
                'placeholder': '0',
            }),
            'precio_local': forms.NumberInput(attrs={
                'class': 'form-control text-end',
                'min': 0,
                'step': 1,
                'placeholder': '0',
            }),
            'precio_domicilio': forms.NumberInput(attrs={
                'class': 'form-control text-end',
                'min': 0,
                'step': 1,
                'placeholder': '0',
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
            'precio_compra': 'Precio de compra (CLP)',
            'precio_local': 'Precio venta local (CLP)',
            'precio_domicilio': 'Precio venta domicilio (CLP)',
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