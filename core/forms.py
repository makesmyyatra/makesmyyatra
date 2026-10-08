from django import forms
from .models import Hotel, RoomType, Booking

class HotelForm(forms.ModelForm):
    class Meta:
        model = Hotel
        fields = [
            'name', 'category', 'city', 'address', 'google_maps_url',
            'star_rating', 'cover_image', 'has_wifi', 'has_parking',
            'has_pool', 'has_restaurant', 'has_ac', 'has_heater',
            'owner_name', 'owner_phone', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'google_maps_url': forms.URLInput(attrs={'class': 'form-control'}),
            'star_rating': forms.NumberInput(attrs={'class': 'form-control'}),
            'cover_image': forms.URLInput(attrs={'class': 'form-control'}),
            'owner_name': forms.TextInput(attrs={'class': 'form-control'}),
            'owner_phone': forms.TextInput(attrs={'class': 'form-control'}),
        }

class RoomTypeForm(forms.ModelForm):
    class Meta:
        model = RoomType
        fields = ['hotel', 'name', 'description', 'max_guests', 'base_price', 'total_inventory', 'is_available']
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