from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mockups', '0029_usuario_totp'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='pedido',
            index=models.Index(fields=['sector'], name='mockups_ped_sector_8a1f2d_idx'),
        ),
        migrations.AddIndex(
            model_name='sobrediario',
            index=models.Index(fields=['fecha_correspondiente'], name='mockups_sob_fecha_c_3e4b5a_idx'),
        ),
        migrations.AddIndex(
            model_name='sobrediario',
            index=models.Index(
                fields=['fecha_correspondiente', 'tipo', 'cerrado'],
                name='mockups_sob_fecha_t_6c7d8e_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='sobrediario',
            index=models.Index(
                fields=['trabajador', 'fecha_correspondiente'],
                name='mockups_sob_trabaj_f_9f0a1b_idx',
            ),
        ),
    ]