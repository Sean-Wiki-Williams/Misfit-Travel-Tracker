from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).strip().lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("A superuser must have is_staff=True and is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    calendar_url = models.TextField(blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class Flight(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="flights")
    flight_key = models.CharField(max_length=64)
    is_manual = models.BooleanField(default=False)
    flight_number = models.CharField(max_length=16)
    origin_code = models.CharField(max_length=3)
    dest_code = models.CharField(max_length=3)
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    equipment = models.CharField(max_length=16)
    details = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "flight_key"), name="unique_user_flight_key")
        ]

    def __str__(self):
        return f"{self.flight_number}: {self.origin_code} to {self.dest_code}"


class ScheduleCache(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="schedule_cache")
    payload = models.JSONField()
    updated_at = models.DateTimeField(default=timezone.now)
