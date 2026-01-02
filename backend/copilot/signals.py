from django.db.models.signals import post_save
from django.dispatch import receiver

from copilot.models import Document
from copilot.tasks import process_document


@receiver(post_save, sender=Document)
def enqueue_document_processing(sender, instance: Document, created: bool, **kwargs):
    # Важно: сработает только на создании документа (не на апдейтах)
    if created and getattr(instance, "status", "") == "uploaded":
        process_document.delay(instance.id)
