# Generated manually — multi-bodega: índices en FK bodega

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mockups', '0035_encrypt_totp_secret'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='pedido',
            index=models.Index(fields=['bodega'], name='mockups_ped_bodega_idx'),
        ),
        migrations.AddIndex(
            model_name='sobrediario',
            index=models.Index(fields=['bodega'], name='mockups_sob_bodega_idx'),
        ),
        migrations.AddIndex(
            model_name='sector',
            index=models.Index(fields=['bodega'], name='mockups_sec_bodega_idx'),
        ),
        migrations.AddIndex(
            model_name='usuario',
            index=models.Index(fields=['bodega'], name='mockups_usr_bodega_idx'),
        ),
    ]
