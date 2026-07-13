# Generated manually — multi-bodega: NOT NULL en FK bodega tras backfill

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mockups', '0036_bodega_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedido',
            name='bodega',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='pedidos',
                to='mockups.bodega',
                verbose_name='Bodega',
                # null=True removido — 0033 ya backfilleó todos los registros
            ),
        ),
        migrations.AlterField(
            model_name='sobrediario',
            name='bodega',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='sobres',
                to='mockups.bodega',
                verbose_name='Bodega',
            ),
        ),
        migrations.AlterField(
            model_name='sector',
            name='bodega',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='sectores',
                to='mockups.bodega',
                verbose_name='Bodega',
            ),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='bodega',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='trabajadores',
                to='mockups.bodega',
                verbose_name='Bodega asignada',
            ),
        ),
    ]
