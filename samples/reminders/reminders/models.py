from django.db import models


class Reminder(models.Model):
    order = models.IntegerField()
    amount = models.IntegerField()
    sent_on = models.DateField()
