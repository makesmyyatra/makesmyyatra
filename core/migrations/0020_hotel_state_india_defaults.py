from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_booking_rooms_count_alter_booking_adults_count_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='hotel',
            name='state',
            field=models.CharField(blank=True, db_index=True, default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='hotel',
            name='city',
            field=models.CharField(db_index=True, default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='emergencysupport',
            name='location',
            field=models.CharField(default='', max_length=200),
        ),
        migrations.AddField(
            model_name='hotelreview',
            name='booking_ref',
            field=models.CharField(blank=True, db_index=True, default='', max_length=50),
        ),
    ]
