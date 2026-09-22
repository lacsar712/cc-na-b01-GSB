def nav(request):
    user = getattr(request, "user", None)
    can_write = bool(
        user and user.is_authenticated and user.groups.filter(name="inspector").exists()
    )
    return {"can_write": can_write}
