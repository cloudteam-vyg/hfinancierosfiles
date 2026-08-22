from django.urls import path

from . import views

app_name = "files"

urlpatterns = [
    # --- Tipos de persona (PersonActivityType) ---
    # NO hay ruta de alta a propósito: este catálogo solo se necesita a media
    # tarea (mientras se da de alta un Cliente o se sube un Archivo), y una
    # pantalla aparte obligaba a abandonar el formulario a medio llenar. El alta
    # vive ÚNICAMENTE en el modal de alta rápida
    # (api/person-activity-types/quick-create/, más abajo); lo que queda aquí es
    # la superficie de mantenimiento, a la que se llega desde el pie de ese modal.
    path("tipos-persona-actividad/", views.PersonActivityTypeListView.as_view(), name="personactivitytype-list"),
    path("tipos-persona-actividad/<int:pk>/editar/", views.PersonActivityTypeUpdateView.as_view(), name="personactivitytype-update"),
    path("tipos-persona-actividad/<int:pk>/eliminar/", views.PersonActivityTypeDeleteView.as_view(), name="personactivitytype-delete"),

    # --- Clientes (Customer) ---
    path("clientes/", views.CustomerListView.as_view(), name="customer-list"),
    path("clientes/nuevo/", views.CustomerCreateView.as_view(), name="customer-create"),
    path("clientes/<int:pk>/editar/", views.CustomerUpdateView.as_view(), name="customer-update"),
    path("clientes/<int:pk>/eliminar/", views.CustomerDeleteView.as_view(), name="customer-delete"),

    # --- Personas (Person) ---
    path("personas/", views.PersonListView.as_view(), name="person-list"),
    path("personas/nuevo/", views.PersonCreateView.as_view(), name="person-create"),
    path("personas/<int:pk>/editar/", views.PersonUpdateView.as_view(), name="person-update"),
    path("personas/<int:pk>/eliminar/", views.PersonDeleteView.as_view(), name="person-delete"),

    # --- Archivos (FileArchive) ---
    path("archivos/", views.file_archive_list_view, name="archive-list"),
    path("archivos/subir/", views.file_archive_upload_view, name="archive-upload"),
    path("archivos/<uuid:pk>/eliminar/", views.FileArchiveDeleteView.as_view(), name="archive-delete"),
    path("archivos/<uuid:pk>/descargar/", views.file_archive_download_view, name="archive-download"),
    path("archivos/<uuid:pk>/editar/", views.FileArchiveUpdateView.as_view(), name="archive-update"),
    path("archivos/<uuid:pk>/preview/", views.file_archive_preview_view, name="archive-preview"),

    # --- Alta rápida (modales de /archivos/subir/ y del alta de cliente) ---
    # Para PersonActivityType esta es la ÚNICA vía de alta que existe.
    path("api/customers/quick-create/", views.customer_quick_create_view, name="customer-quick-create"),
    path("api/archive-classes/quick-create/", views.archive_class_quick_create_view, name="archive-class-quick-create"),
    path("api/person-activity-types/quick-create/", views.person_activity_type_quick_create_view, name="personactivitytype-quick-create"),
]
