from django.contrib import admin
from django.template.defaultfilters import filesizeformat

from rangefilter.filters import DateTimeRangeFilter
from simple_history.admin import SimpleHistoryAdmin

from .forms import FileArchiveAdminForm
from .models import (
    ClassName, ActivityType, PersonType, Customer, Person, ArchiveClass, FileArchive,
)
from .uploads import stamp_upload_metadata

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'email', 'phone_number', 'activity_type', 'person_type', 'date_of_constitution')
    search_fields = ('name', 'group', 'email', 'phone_number')
    list_filter = ('activity_type', 'person_type')


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('name', 'position', 'email', 'phone_number', 'customer')
    search_fields = ('name', 'position', 'email', 'phone_number')
    list_filter = ('customer',)

# Se omiten upload_status y los campos del antiguo pipeline asíncrono
# (celery_task_id, processed_at, error_message, error_traceback): siguen en la
# base de datos pero ya no los escribe nadie, así que mostrarlos solo confunde.
READONLY_ON_EDIT = (
    "original_filename", "file", "file_size", "content_type",
    "uploaded_by", "uploaded_at", "updated_at",
)


@admin.register(FileArchive)
class FileArchiveAdmin(SimpleHistoryAdmin):
    form = FileArchiveAdminForm
    list_display = ('name', 'customer', 'archive_class', 'readable_size', 'uploaded_by', 'uploaded_at')
    list_filter = ('archive_class', 'customer', 'uploaded_by', ('uploaded_at', DateTimeRangeFilter))
    search_fields = ('name', 'original_filename', 'customer__name', 'contact')

    def get_fields(self, request, obj=None):
        base = ["archive_class", "customer", "name", "contact", "opening_date", "due_date"]
        if obj is None:
            return base + ["file"]
        return base + list(READONLY_ON_EDIT)

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else READONLY_ON_EDIT

    def readable_size(self, obj):
        return filesizeformat(obj.file_size) if obj.file_size else "-"
    readable_size.short_description = "Tamaño"

    def save_model(self, request, obj, form, change):
        if not change:
            # Misma regla que el frontend propio (files/views.py): el form ya
            # exige el archivo en alta, así que aquí siempre viene.
            uploaded = form.cleaned_data.get("file")
            if uploaded:
                stamp_upload_metadata(obj, uploaded, request.user)
            else:
                obj.uploaded_by = request.user

        super().save_model(request, obj, form, change)

        if not change:
            self.message_user(request, f"«{obj.name}» subido.")


@admin.register(ClassName)
class ClassNameAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(PersonType)
class PersonTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)




@admin.register(ArchiveClass)
class ArchiveClassAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

