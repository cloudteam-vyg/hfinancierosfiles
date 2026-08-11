from django.urls import path

from . import views

app_name = "files"

urlpatterns = [
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
    path("archivos/upload-sas/", views.file_archive_upload_sas_view, name="archive-upload-sas"),
    path("archivos/estado/", views.file_archive_status_view, name="archive-status"),
    path("archivos/<uuid:pk>/eliminar/", views.FileArchiveDeleteView.as_view(), name="archive-delete"),
]
