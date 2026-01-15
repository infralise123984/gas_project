# mockups/forms.py
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import Pedido, DetallePedido, TipoBalon

SECTORES = [
    ("", "— Seleccionar sector —"),
    ("Población Dintrans", "Población Dintrans"),
    ("Machalí Alto", "Machalí Alto"),
    ("Gultro", "Gultro"),
    ("Villa Los Tilos", "Villa Los Tilos"),
    ("Centro Rancagua", "Centro Rancagua"),
    ("Baquedano", "Baquedano"),
    ("La Granja", "La Granja"),
    ("Rancagua Norte", "Rancagua Norte"),
    ("Villa Teniente", "Villa Teniente"),
    ("Requínoa", "Requínoa"),
    ("Graneros", "Graneros"),
    ("Mostazal", "Mostazal"),
    ("Codegua", "Codegua"),
    ("Otro", "Otro (especificar en dirección)"),
]


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


# Formset - ajustado para permitir formularios vacíos inicialmente
DetalleFormSet = inlineformset_factory(
    Pedido,
    DetallePedido,
    form=DetallePedidoForm,
    formset=BaseDetalleFormSet,
    extra=1,  # Mostrar 1 formulario vacío inicialmente
    can_delete=True,
    min_num=0,  # No forzar mínimo en el formset base
    validate_min=False,  # La validación la hacemos en clean()
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