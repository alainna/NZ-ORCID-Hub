# -*- coding: utf-8 -*-
"""Application models."""

from collections import namedtuple
from hashlib import md5
from itertools import zip_longest
from urllib.parse import urlencode

from flask_login import UserMixin
from peewee import (BooleanField, CharField, CompositeKey, DateTimeField,
                    Field, ForeignKeyField, Model, OperationalError,
                    SmallIntegerField, TextField, datetime)

from application import db

try:
    from enum import IntFlag
except ImportError:
    from enum import IntEnum as IntFlag


class PartialDate(namedtuple("PartialDate", ["year", "month", "day"])):
    """Partial date (without month day or both moth and month day."""

    def as_orcid_dict(self):
        """Return ORCID dictionry representation of the partial date."""
        if self.year is None and self.month is None and self.day is None:
            return None
        return dict(((f, None if v is None else {
            "value": ("%04d" if f == "year" else "%02d") % v
        }) for (f, v) in zip(self._fields, self)))

    @classmethod
    def create(cls, dict_value):
        """Create a partial date form ORCID dictionary representation.

        >>> PartialDate.create({"year": {"value": "2003"}}).as_orcid_dict()
        {'year': {'value': '2003'}, 'month': None, 'day': None}

        >>> PartialDate.create({"year": {"value": "2003"}}).year
        2003
        """
        if dict_value is None or dict_value == {}:
            return None
        return cls(**{
            k: int(v.get("value")) if v else None
            for k, v in dict_value.items()
        })

    def as_datetime(self):
        return datetime.datetime(self.year, self.month, self.day)


PartialDate.__new__.__defaults__ = (None, ) * len(PartialDate._fields)


class PartialDateField(Field):
    """Partial date custom DB data field mapped to varchar(10)."""

    db_field = 'varchar(10)'

    def db_value(self, value):
        """Convert into partial ISO date textual representation: YYYY-**-**, YYYY-MM-**, or YYYY-MM-DD."""
        if value is None or not value.year:
            return None
        else:
            res = "%04d" % int(value.year)
            if value.month:
                res += "-%02d" % int(value.month)
            else:
                return res + "-**-**"
            return res + "-%02d" % int(value.day) if value.day else res + "-**"

    def python_value(self, value):
        """Parse partial ISO date textual representation."""
        if value is None:
            return None

        parts = [int(p) for p in value.split("-") if "*" not in p]
        return PartialDate(
            **dict(zip_longest(("year", "month", "day", ), parts)))


class Role(IntFlag):
    """
    Enum used to represent user role.

    The model provide multi role support representing role sets as bitmaps.
    """

    NONE = 0  # NONE
    SUPERUSER = 1  # SuperUser
    ADMIN = 2  # Admin
    RESEARCHER = 4  # Researcher
    ANY = 255  # ANY

    def __eq__(self, other):
        if isinstance(other, Role):
            return self.value == other.value
        return (self.name == other or
                self.name == getattr(other, 'name', None))

    def __hash__(self):
        return hash(self.name)


class BaseModel(Model):
    class Meta:
        database = db


class Organisation(BaseModel):
    """
    Research oranisation
    """

    name = CharField(max_length=100, unique=True)
    email = CharField(max_length=80, unique=True, null=True)
    tuakiri_name = CharField(max_length=80, unique=True, null=True)
    orcid_client_id = CharField(max_length=80, unique=True, null=True)
    orcid_secret = CharField(max_length=80, unique=True, null=True)
    confirmed = BooleanField(default=False)
    country = CharField(null=True)
    city = CharField(null=True)
    disambiguation_org_id = CharField(null=True)
    disambiguation_org_source = CharField(null=True)

    @property
    def users(self):
        """
        Organisation's users (query)
        """
        return User.select().join(
            self.userorg_set.alias("sq"),
            on=(self.userorg_set.c.user_id == User.id))

    @property
    def admins(self):
        """
        Organisation's adminstrators (query)
        """
        return User.select().join(
            self.userorg_set.where(self.userorg_set.c.is_admin).alias("sq"),
            on=(self.userorg_set.c.user_id == User.id))

    def __repr__(self):
        return self.name


class User(BaseModel, UserMixin):
    """
    ORCiD Hub user (incling researchers, organisation administrators, hub administrators, etc.)
    """

    name = CharField(max_length=64, null=True)
    first_name = CharField(null=True, verbose_name="Firs Name")
    last_name = CharField(null=True, verbose_name="Last Name")
    email = CharField(max_length=120, unique=True, null=True)
    edu_person_shared_token = CharField(
        max_length=120,
        unique=True,
        verbose_name="EDU Person Shared Token",
        null=True)
    # ORCiD:
    orcid = CharField(
        max_length=120, unique=True, verbose_name="ORCID", null=True)
    confirmed = BooleanField(default=False)
    # Role bit-map:
    roles = SmallIntegerField(default=0)
    edu_person_affiliation = TextField(
        null=True, verbose_name="EDU Person Affiliations")
    tech_contact = BooleanField(default=False)

    # TODO: many-to-many
    # NB! depricated!
    # TODO: we still need to rememeber the rognanistiaon that last authenticated the user
    organisation = ForeignKeyField(
        Organisation, related_name="members", on_delete="CASCADE", null=True)

    @property
    def organisations(self):
        """
        All linked to the user organisation query
        """
        return Organisation.select().join(
            self.userorg_set.alias("sq"),
            on=Organisation.id == self.userorg_set.c.org_id)

    @property
    def admin_for(self):
        """
        Organisations the user is admin for (query)
        """
        return Organisation.select().join(
            self.userorg_set.where(self.userorg_set.c.is_admin).alias("sq"),
            on=Organisation.id == self.userorg_set.c.org_id)

    username = CharField(max_length=64, unique=True, null=True)
    password = TextField(null=True)

    @property
    def is_active(self):
        # TODO: confirmed - user that email is cunfimed either by IdP or by confirmation email
        # ins't the same as "is active"
        return self.confirmed

    def has_role(self, role):
        """Returns `True` if the user identifies with the specified role.

        :param role: A role name, `Role` instance, or integer value"""
        if isinstance(role, Role):
            return role & Role(self.roles)
        elif isinstance(role, str):
            try:
                return Role[role.upper()] & Role(self.roles)
            except:
                False
        elif type(role) is int:
            return role & self.roles
        else:
            return False

    @property
    def is_superuser(self):
        return self.roles & Role.SUPERUSER

    @property
    def is_admin(self):
        return self.roles & Role.ADMIN

    def avatar(self, size=40, default="identicon"):
        """Return Gravatar service user avatar URL."""
        # TODO: default gravatar image
        # default = "https://www.example.com/default.jpg"
        gravatar_url = "https://www.gravatar.com/avatar/" + md5(
            self.email.lower().encode()).hexdigest() + "?"
        gravatar_url += urlencode({'d': default, 's': str(size)})
        return gravatar_url

    @property
    def gravatar_profile_url(self):
        """Return Gravatar service user profile URL."""
        return "https://www.gravatar.com/" + md5(
            self.email.lower().encode()).hexdigest()


class UserOrg(BaseModel):
    """Linking object for many-to-many relationship."""

    user = ForeignKeyField(User, on_delete="CASCADE")
    org = ForeignKeyField(
        Organisation,
        index=True,
        on_delete="CASCADE",
        verbose_name="Organisation")

    is_admin = BooleanField(
        default=False,
        help_text="User is an administrator for the organisation")

    # TODO: the access token should be either here or in a saparate list
    # access_token = CharField(max_length=120, unique=True, null=True)

    class Meta:
        db_table = "user_org"
        table_alias = "oa"
        primary_key = CompositeKey("user", "org")


class OrcidToken(BaseModel):
    """
    For Keeping Orcid token in the table.
    """

    user = ForeignKeyField(User)
    org = ForeignKeyField(
        Organisation, index=True, verbose_name="Organisation")
    scope = TextField(null=True)
    access_token = CharField(max_length=36, unique=True, null=True)
    issue_time = DateTimeField(default=datetime.datetime.now)
    refresh_token = CharField(max_length=36, unique=True, null=True)
    expires_in = SmallIntegerField(default=0)


class User_Organisation_affiliation(BaseModel):
    """
    For Keeping the information about the affiliation
    """

    user = ForeignKeyField(User)
    organisation = ForeignKeyField(
        Organisation, index=True, verbose_name="Organisation")
    name = TextField(null=True, verbose_name="Institution/employer")
    start_date = PartialDateField(null=True)
    end_date = PartialDateField(null=True)
    department_name = TextField(null=True)
    department_city = TextField(null=True)
    role_title = TextField(null=True)
    put_code = SmallIntegerField(default=0, null=True)
    path = TextField(null=True)


def create_tables():
    """Create all DB tables."""
    try:
        db.connect()
    except OperationalError:
        pass
    models = (Organisation, User, UserOrg, OrcidToken,
              User_Organisation_affiliation)
    db.create_tables(models)


def drop_tables():
    """Drop all model tables."""
    for m in (Organisation, User, UserOrg, OrcidToken,
              User_Organisation_affiliation):
        if m.table_exists():
            try:
                m.drop_table(fail_silently=True, cascade=db.drop_cascade)
            except OperationalError:
                pass