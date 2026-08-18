from django import forms

from .models import ActivityType, ArchiveClass, ClassName, Customer, FileArchive, Person
from .uploads import validate_upload_size

DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


def _customer_choices():
    """Queryset para todo <select> de cliente.

    Customer.__str__ toca self.activity_type.name, así que sin
    select_related cada <option> dispara una query aparte (N+1). El
    order_by fija el orden de las opciones, que Customer no declara en
    Meta a propósito (sus ListViews ya lo hacen por su cuenta).

    Función plana y no una fábrica de formularios ni un mixin: ver
    ARCHITECTURE.md ("una fábrica rompería `grep`") -- así cada __init__
    sigue diciendo explícitamente qué hace.
    """
    return Customer.objects.select_related("activity_type").order_by("name")


class FileArchiveUploadForm(forms.ModelForm):
    class Meta:
        model = FileArchive
        fields = ("archive_class", "customer", "name", "opening_date", "due_date", "file")
        widgets = {
            "file": forms.ClearableFileInput(attrs={"hidden": True}),
            "opening_date": DATE_INPUT,
            "due_date": DATE_INPUT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FileArchive.file es blank=True (el form de edición lo excluye), así
        # que el ModelForm lo haría opcional: enviar la subida sin archivo
        # llegaba a la vista con cleaned_data["file"] = None y reventaba en
        # uploaded.name (500). Aquí sí es obligatorio.
        self.fields["file"].required = True
        self.fields["file"].error_messages["required"] = "Selecciona un archivo para subir."
        self.fields["customer"].queryset = _customer_choices()

    def clean_file(self):
        uploaded = self.cleaned_data.get("file")
        if uploaded:
            validate_upload_size(uploaded)
        return uploaded


class FileArchiveEditForm(forms.ModelForm):
    """Edición de metadatos desde /archivos/<id>/editar/.

    Excluye `file` a propósito: el archivo subido es inmutable después de
    la subida (mismo criterio que READONLY_ON_EDIT en files/admin.py) --
    reemplazarlo es borrar y volver a subir, no un campo de este form.
    """

    class Meta:
        model = FileArchive
        fields = ("archive_class", "customer", "name", "opening_date", "due_date")
        widgets = {"opening_date": DATE_INPUT, "due_date": DATE_INPUT}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = _customer_choices()


class PersonForm(forms.ModelForm):
    """Alta y edición de Persona desde /personas/.

    Existe solo para poder fijar el queryset de `customer`: con
    `fields = (...)` en la vista, Django construye el ModelForm con
    Customer.objects.all() y el <select> vuelve al N+1 de
    Customer.__str__.
    """

    class Meta:
        model = Person
        fields = ("customer", "name", "position", "email", "phone_number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = _customer_choices()


class QuickCustomerForm(forms.ModelForm):
    """Alta mínima de Customer desde el modal "+ Nuevo" de /archivos/subir/.

    Los cuatro campos son obligatorios en el modelo (ver files/models.py), no
    por comodidad del modal: el ModelForm los exige igual que cualquier otra
    alta de Customer. En particular `name` ya no necesita forzarse a
    required=True aquí -- lo deriva del modelo desde la migración 0003, y
    volver a ponerlo a mano solo duplicaría el invariante en un segundo sitio
    donde puede quedarse desactualizado.
    """

    class Meta:
        model = Customer
        fields = ("name", "classname", "activity_type", "date_of_constitution")
        widgets = {"date_of_constitution": DATE_INPUT}


# Catálogos que exige el alta de Cliente (y de Archivo). Se pueden crear desde
# un modal en el propio formulario para no tener que abandonarlo a medio
# llenar; de hecho es la ÚNICA forma de crearlos en el frontend (no hay página
# de alta aislada, ver files/urls.py).
class _QuickCatalogForm(forms.ModelForm):
    """Base de los tres catálogos: mismos campos y misma regla de duplicados.

    `name` no lleva unique=True en la base de datos (ver la deuda anotada en
    ARCHITECTURE.md). Con el alta a un clic desde tres sitios distintos, y
    ahora también desde dentro de otro modal, dos "Comercio" se crean sin
    esfuerzo y el <select> acaba mostrando dos opciones idénticas con pk
    distinto. Se valida aquí, en el formulario, porque una constraint nueva
    exigiría migrar datos que ya pueden tener duplicados.
    """

    def clean_name(self):
        name = self.cleaned_data["name"]
        existentes = self._meta.model.objects.filter(name__iexact=name)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise forms.ValidationError(f'Ya existe un registro con el nombre "{name}".')
        return name


class QuickArchiveClassForm(_QuickCatalogForm):
    class Meta:
        model = ArchiveClass
        fields = ("name", "description")


class QuickClassNameForm(_QuickCatalogForm):
    class Meta:
        model = ClassName
        fields = ("name", "description")


class QuickActivityTypeForm(_QuickCatalogForm):
    class Meta:
        model = ActivityType
        fields = ("name", "description")
