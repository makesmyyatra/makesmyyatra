from django.core.exceptions import ValidationError


ALLOWED_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']
ALLOWED_DOC_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png']
MAX_FILE_SIZE_MB = 5
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


def validate_uploaded_file(file_obj, allowed_extensions, max_size_mb=5):
    """
    Reusable file validator.
    Returns error string if invalid, None if valid.
    """
    if not file_obj:
        return None
    
    # Size check
    if file_obj.size > max_size_mb * 1024 * 1024:
        return f"'{file_obj.name}' is too large ({file_obj.size // (1024*1024)}MB). Max {max_size_mb}MB allowed."
    
    # Extension check
    ext = file_obj.name.rsplit('.', 1)[-1].lower() if '.' in file_obj.name else ''
    if ext not in allowed_extensions:
        return f"'{file_obj.name}' has invalid format. Allowed: {', '.join(allowed_extensions)}."
    
    return None


def validate_image(file_obj):
    return validate_uploaded_file(file_obj, ALLOWED_IMAGE_EXTENSIONS, MAX_FILE_SIZE_MB)


def validate_doc(file_obj):
    return validate_uploaded_file(file_obj, ALLOWED_DOC_EXTENSIONS, MAX_FILE_SIZE_MB)