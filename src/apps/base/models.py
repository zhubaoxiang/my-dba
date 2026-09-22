from django.db import models

# Create your models here.


class AbstractTimeFiledModel(models.Model):
    """
    每个数据库所用的时间列
    """

    create_time = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    creator = models.CharField(default="", max_length=32, verbose_name="创建人")
    is_deleted = models.BooleanField(default=False, verbose_name="是否删除")

    class Meta:
        abstract = True
