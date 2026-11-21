from services.security_utils import is_valid_email, normalize_email

AGE_GROUPS = ("adult", "minor")


def parse_age_form(form, own_email=None):
    age_group = (form.get("age_group") or "").strip()
    if age_group not in AGE_GROUPS:
        return None, None, "Please tell us whether you are 18 or older."
    if age_group == "adult":
        return "adult", None, None
    parent_email = normalize_email(form.get("parent_email"))
    if not is_valid_email(parent_email):
        return None, None, "Please enter a valid parent or guardian email address."
    if own_email and parent_email == normalize_email(own_email):
        return None, None, "The parent or guardian email must be different from your own."
    return "minor", parent_email, None