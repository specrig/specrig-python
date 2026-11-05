from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    operations = [
        migrations.CreateModel(
            name="Reminder",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("order", models.IntegerField()),
                ("amount", models.IntegerField()),
                ("sent_on", models.DateField()),
            ],
        ),
    ]
