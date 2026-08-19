from django import forms

from .models import FileArchive
from .uploads import validate_upload_size


class FileArchiveAdminForm(forms.ModelForm):
    class Meta:
        model = FileArchive
        fields = ("customer", "archive_class", "name", "contact", "opening_date", "due_date", "file")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # En alta el archivo es obligatorio: sin esto el Admin creaba filas
        # sin archivo en silencio (save_model las deja pasar con `if uploaded`)
        # y encolaba post-procesamiento sobre ellas. En edición el campo ni
        # siquiera está en el form (ver READONLY_ON_EDIT en files/admin.py).
        if self.instance._state.adding and "file" in self.fields:
            self.fields["file"].required = True

    def clean_file(self):
        uploaded = self.cleaned_data.get("file")
        if uploaded and self.instance._state.adding:
            validate_upload_size(uploaded)
        return uploaded
