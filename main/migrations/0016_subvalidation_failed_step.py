from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_company_domain'),
    ]

    operations = [
        migrations.AddField(
            model_name='subvalidation',
            name='failed_step',
            field=models.IntegerField(null=True, blank=True),
        ),
    ]
