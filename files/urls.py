from django.urls import path

from . import views

app_name = "files"

urlpatterns = [
    # --- Clases de cliente (ClassName) ---
    path("clases-cliente/", views.ClassNameListView.as_view(), name="classname-list"),
    path("clases-cliente/nueva/", views.ClassNameCreateView.as_view(), name="classname-create"),
    path("clases-cliente/<int:pk>/editar/", views.ClassNameUpdateView.as_view(), name="classname-update"),
    path("clases-cliente/<int:pk>/eliminar/", views.ClassNameDeleteView.as_view(), name="classname-delete"),

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

    # --- Alta rápida (modales de /archivos/subir/) ---
    path("api/customers/quick-create/", views.customer_quick_create_view, name="customer-quick-create"),
    path("api/archive-classes/quick-create/", views.archive_class_quick_create_view, name="archive-class-quick-create"),
]
