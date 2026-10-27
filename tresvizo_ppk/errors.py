"""Errores que ve el usuario (especificación §8).

`UserError` lleva una frase en español lista para enseñarse tal cual en la interfaz o en la línea de órdenes; cualquier
otra excepción es un error de programa.
"""
from __future__ import annotations


class UserError(Exception):
    """Algo que el usuario puede entender y corregir: el mensaje va en español, sin jerga de programa."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class Cancelled(UserError):
    """El usuario canceló la operación (el `threading.Event` de cancelar se activó)."""

    def __init__(self, message: str = "Cancelado."):
        super().__init__(message)
