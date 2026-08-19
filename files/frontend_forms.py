from django import forms

from .models import (
    ActivityType, ArchiveClass, ClassName, Customer, FileArchive, Person, PersonType,
)
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


class CustomerForm(forms.ModelForm):
    """Alta y edición de Cliente. ÚNICO formulario de Cliente de la app.

    Lo usan las dos superficies de alta que existen:

      - /clientes/nuevo/ y /clientes/<pk>/editar/ (CustomerCreateView/UpdateView)
      - el modal "+ Nuevo cliente" de /archivos/subir/ (customer_quick_create_view)

    Que sea uno solo es el arreglo de un bug de divergencia, no una preferencia
    de estilo: el modal tenía su propio QuickCustomerForm de 4 campos, así que
    `person_type` y `notes` llegaron al modelo y solo aparecieron en la pantalla
    dedicada. Quien daba de alta un cliente desde la subida capturaba menos
    datos y no tenía forma de saberlo.

    Existe también para poder declarar widgets, que es lo único que
    `fields = (...)` en la vista no permite: date_of_constitution es un DateField
    desde siempre, pero con el DateInput por defecto de Django se renderizaba
    como <input type="text"> y había que teclear la fecha en un formato que la
    pantalla no decía en ninguna parte.

    La lista de campos vive AQUÍ y en ningún otro sitio: antes estaba en la
    constante CUSTOMER_FIELDS de files/views.py, duplicada para Create y
    Update.
    """

    class Meta:
        model = Customer
        fields = (
            "classname", "name", "group", "email", "phone_number", "address",
            "country", "activity_type", "person_type", "date_of_constitution",
            "web_site", "word_clave", "notes",
        )
        widgets = {
            # format="%Y-%m-%d" no es decorativo: sin él, al editar un cliente
            # Django serializa la fecha con el formato de es-mx ("19 de agosto
            # de 2026"), un <input type="date"> no lo entiende y el campo sale
            # VACÍO -- guardar entonces borraría el dato.
            "date_of_constitution": DATE_INPUT,
            # El maxlength=300 lo pone Django solo, derivado del max_length del
            # modelo (CharField.widget_attrs lo emite también para Textarea):
            # escribirlo aquí a mano sería duplicar el tope del servidor.
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def wide_field_names(self):
        """Campos que ocupan las dos columnas de la rejilla del modal.

        DERIVADO del widget (todo Textarea) y no de una lista escrita a mano:
        un TextField nuevo en el modelo se coloca a ancho completo por sí solo,
        sin que nadie tenga que acordarse de apuntarlo aquí. Lo consume
        quick_create_customer_fields.html; en la pantalla dedicada, que es de
        una sola columna, no aplica.
        """
        return [
            name for name, field in self.fields.items()
            if isinstance(field.widget, forms.Textarea)
        ]


class FileArchiveUploadForm(forms.ModelForm):
    class Meta:
        model = FileArchive
        fields = ("archive_class", "customer", "name", "contact", "opening_date", "due_date", "file")
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
        fields = ("archive_class", "customer", "name", "contact", "opening_date", "due_date")
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


# NO hay QuickCustomerForm: el modal "+ Nuevo cliente" de /archivos/subir/ usa
# el mismo CustomerForm de arriba. Existió como "alta mínima" de 4 campos y fue
# precisamente esa duplicación la que dejó el modal atrás cuando el modelo creció
# (`person_type` y `notes` solo llegaron a la pantalla dedicada). Si vuelve a
# hacer falta un subconjunto de campos, que sea un `fields` acotado sobre este
# mismo form y no un segundo ModelForm que pueda derivar otra vez.


# Catálogos usados por el alta de Cliente (y de Archivo). Se pueden crear desde
# un modal en el propio formulario para no tener que abandonarlo a medio
# llenar; de hecho es la ÚNICA forma de crearlos en el frontend (no hay página
# de alta aislada, ver files/urls.py).
class _QuickCatalogForm(forms.ModelForm):
    """Base de los cuatro catálogos: mismos campos y misma regla de duplicados.

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


class QuickPersonTypeForm(_QuickCatalogForm):
    class Meta:
        model = PersonType
        fields = ("name", "description")
