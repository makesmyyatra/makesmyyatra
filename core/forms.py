from django import forms
from .models import Hotel, RoomType, MealPlan, Coupon, Booking, EmergencySupport

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
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Hotel Name'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Full Address'}),
            'google_maps_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'Google Maps Link'}),
            'star_rating': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 5}),
            'cover_image': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'Cover Image URL'}),
            'owner_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Property Manager / Owner'}),
            'owner_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Contact Number'}),
            'has_wifi': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'has_parking': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'has_pool': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'has_restaurant': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'has_ac': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'has_heater': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class RoomTypeForm(forms.ModelForm):
    class Meta:
        model = RoomType
        fields = ['hotel', 'name', 'description', 'max_guests', 'base_price', 'total_inventory', 'is_available']
        widgets = {
            'hotel': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Deluxe AC Room'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Room details'}),
            'max_guests': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'base_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'total_inventory': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'is_available': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class BookingStatusForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ['status', 'is_paid']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }