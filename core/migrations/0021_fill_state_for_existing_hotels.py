from django.db import migrations

def fill_state(apps, schema_editor):
    Hotel = apps.get_model('core', 'Hotel')
    # Every hotel created before the All-India update is in Himachal Pradesh
    Hotel.objects.filter(state='').update(state='Himachal Pradesh')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_hotel_state_india_defaults'),
    ]

    operations = [
        migrations.RunPython(fill_state, migrations.RunPython.noop),
    ]
