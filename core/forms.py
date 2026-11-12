from django import forms
from .models import Hotel, RoomType, Booking
from django.core.validators import FileExtensionValidator
from django.core.exceptions import ValidationError


def validate_file_size(value):
    limit = 5 * 1024 * 1024  # 5 MB
    if value.size > limit:
        raise ValidationError('File too large. Size should not exceed 5 MB.')


ALLOWED_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png']

ALLOWED_CONTENT_TYPES = [
    'application/pdf',
    'image/jpeg',
    'image/jpg',
    'image/png',
]


class HotelForm(forms.ModelForm):
    verification_doc = forms.FileField(
        required=False,
        validators=[
            FileExtensionValidator(allowed_extensions=ALLOWED_EXTENSIONS),
            validate_file_size,
        ],
        help_text='PDF, JPG, or PNG (max 5 MB)',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf,.jpg,.jpeg,.png',
        }),
    )

    class Meta:
        model = Hotel
        fields = [
            'name', 'category', 'city', 'state', 'address', 'google_maps_url',
            'star_rating', 'cover_image', 'verification_doc',
            'has_wifi', 'has_parking', 'has_pool', 'has_restaurant',
            'has_ac', 'has_heater',
            'owner_name', 'owner_phone',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'google_maps_url': forms.URLInput(attrs={'class': 'form-control'}),
            'star_rating': forms.NumberInput(attrs={'class': 'form-control'}),
            'cover_image': forms.URLInput(attrs={'class': 'form-control'}),
            'owner_name': forms.TextInput(attrs={'class': 'form-control'}),
            'owner_phone': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_verification_doc(self):
        """Extra safety: validate MIME type (not just extension)."""
        doc = self.cleaned_data.get('verification_doc')
        if doc:
            content_type = getattr(doc, 'content_type', '')
            if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                raise ValidationError(
                    f'Invalid file type ({content_type}). '
                    f'Only PDF, JPG, or PNG allowed.'
                )
        return doc


class RoomTypeForm(forms.ModelForm):
    class Meta:
        model = RoomType
        fields = [
            'hotel', 'name', 'description', 'max_guests',
            'base_price', 'total_inventory', 'is_available',
        ]
        widgets = {
            'hotel': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'max_guests': forms.NumberInput(attrs={'class': 'form-control'}),
            'base_price': forms.NumberInput(attrs={'class': 'form-control'}),
            'total_inventory': forms.NumberInput(attrs={'class': 'form-control'}),
        }


class BookingStatusForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ['payment_status']
        widgets = {
            'payment_status': forms.Select(attrs={'class': 'form-select'}),
        }