from django import forms

from .models import ActivityType, ArchiveClass, ClassName, Customer, FileArchive
from .uploads import validate_upload_size

DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


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
        # Customer.__str__ toca self.activity_type.name -- sin esto, cada
        # opción del <select> dispara una query aparte (N+1).
        self.fields["customer"].queryset = (
            Customer.objects.select_related("activity_type").order_by("name")
        )


class QuickCustomerForm(forms.ModelForm):
    """Alta mínima de Customer desde el modal "+ Nuevo" de /archivos/subir/.

    classname/activity_type/date_of_constitution son NOT NULL en el modelo
    (ver files/models.py) -- no son opcionales aquí solo por comodidad del
    modal, el ModelForm los exige igual que cualquier alta de Customer.
    """

    class Meta:
        model = Customer
        fields = ("name", "classname", "activity_type", "date_of_constitution")
        widgets = {"date_of_constitution": DATE_INPUT}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A diferencia del alta completa (CustomerCreateView), aquí "name"
        # sí es obligatorio: es lo único que identifica al cliente en el
        # <select> del formulario de subida.
        self.fields["name"].required = True


class QuickArchiveClassForm(forms.ModelForm):
    class Meta:
        model = ArchiveClass
        fields = ("name",)


# Catálogos que exige el alta de Cliente. Se pueden crear desde un modal en el
# propio formulario para no tener que abandonarlo a medio llenar.
class QuickClassNameForm(forms.ModelForm):
    class Meta:
        model = ClassName
        fields = ("name",)


class QuickActivityTypeForm(forms.ModelForm):
    class Meta:
        model = ActivityType
        fields = ("name",)
