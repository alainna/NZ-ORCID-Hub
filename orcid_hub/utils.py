# -*- coding: utf-8 -*-
"""Various utilities."""

import json
import logging
import os
from datetime import datetime, timedelta
from itertools import filterfalse, groupby
from urllib.parse import quote, urlencode, urlparse

import emails
import flask
import requests
from flask import request, url_for
from flask_login import current_user
from html2text import html2text
from itsdangerous import URLSafeTimedSerializer
from peewee import JOIN

from . import app, orcid_client
from .models import (AFFILIATION_TYPES, Affiliation, AffiliationRecord, FundingInvitees,
                     FundingRecord, OrcidToken, Organisation, Role, Task, TaskType, Url, User,
                     UserInvitation, UserOrg, WorkInvitees, WorkRecord, db)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

EDU_CODES = {"student", "edu"}
EMP_CODES = {"faculty", "staff", "emp"}

ENV = app.config.get("ENV")
EXTERNAL_SP = app.config.get("EXTERNAL_SP")


def get_next_url():
    """Retrieve and sanitize next/return URL."""
    _next = request.args.get("next") or request.args.get("_next") or request.args.get("url")

    if _next and ("orcidhub.org.nz" in _next or _next.startswith("/") or "127.0" in _next
                  or "c9users.io" in _next):
        return _next
    return None


def is_valid_url(url):
    """Validate URL (expexted to have a path)."""
    try:
        result = urlparse(url)
        return result.scheme and result.netloc and result.path
    except:
        return False


def send_email(template_filename,
               recipient,
               cc_email=None,
               sender=(app.config.get("APP_NAME"), app.config.get("MAIL_DEFAULT_SENDER")),
               reply_to=None,
               subject=None,
               base=None,
               logo=None,
               org=None,
               **kwargs):
    """Send an email, acquiring its payload by rendering a jinja2 template.

    :type template_filename: :class:`str`
    :param subject: the subject of the email
    :param base: the base template of the email messagess
    :param template_filename: name of the template_filename file in ``templates/emails`` to use
    :type recipient: :class:`tuple` (:class:`str`, :class:`str`)
    :param recipient: 'To' (name, email)
    :type sender: :class:`tuple` (:class:`str`, :class:`str`)
    :param sender: 'From' (name, email)
    :param org: organisation on which behalf the email is sent
    * `recipient` and `sender` are made available to the template as variables
    * In any email tuple, name may be ``None``
    * The subject is retrieved from a sufficiently-global template variable;
      typically set by placing something like
      ``{% set subject = "My Subject" %}``
      at the top of the template used (it may be inside some blocks
      (if, elif, ...) but not others (rewrap, block, ...).
      If it's not present, it defaults to "My Subject".
    * With regards to line lengths: :class:`email.mime.text.MIMEText` will
      (at least, in 2.7) encode the body of the text in base64 before sending
      it, text-wrapping the base64 data. You will therefore not have any
      problems with SMTP line length restrictions, and any concern to line
      lengths is purely aesthetic or to be nice to the MUA.
      :class:`RewrapExtension` may be used to wrap blocks of text nicely.
      Note that ``{{ variables }}`` in manually wrapped text can cause
      problems!
    """
    if not org and current_user and not current_user.is_anonymous:
        org = current_user.organisation
    if not template_filename.endswith(".html"):
        template_filename += ".html"
    jinja_env = flask.current_app.jinja_env

    if logo is None:
        if org and org.logo:
            logo = url_for("logo_image", token=org.logo.token, _external=True)
        else:
            logo = url_for("static", filename="images/banner-small.png", _external=True)

    if not base and org:
        if org.email_template_enabled and org.email_template:
            base = org.email_template

    if not base:
        base = app.config.get("DEFAULT_EMAIL_TEMPLATE")

    jinja_env = jinja_env.overlay(autoescape=False)

    def _jinja2_email(name, email):
        if name is None:
            hint = 'name was not set for email {0}'.format(email)
            name = jinja_env.undefined(name='name', hint=hint)
        return {"name": name, "email": email}

    template = jinja_env.get_template(template_filename)

    kwargs["sender"] = _jinja2_email(*sender)
    kwargs["recipient"] = _jinja2_email(*recipient)
    if subject is not None:
        kwargs["subject"] = subject
    if reply_to is None:
        reply_to = sender

    rendered = template.make_module(vars=kwargs)
    if subject is None:
        subject = getattr(rendered, "subject", "Welcome to the NZ ORCID Hub")

    html_msg = base.format(
        EMAIL=kwargs["recipient"]["email"],
        SUBJECT=subject,
        MESSAGE=str(rendered),
        LOGO=logo,
        BASE_URL=url_for("index", _external=True)[:-1],
        INCLUDED_URL=kwargs.get("invitation_url", '') or kwargs.get("include_url", ''))

    plain_msg = html2text(html_msg)

    msg = emails.html(
        subject=subject,
        mail_from=(app.config.get("APP_NAME", "ORCID Hub"), app.config.get("MAIL_DEFAULT_SENDER")),
        html=html_msg,
        text=plain_msg)
    dkip_key_path = app.config["DKIP_KEY_PATH"]
    if os.path.exists(dkip_key_path):
        msg.dkim(key=open(dkip_key_path), domain="orcidhub.org.nz", selector="default")
    if cc_email:
        msg.cc.append(cc_email)
    msg.set_headers({"reply-to": reply_to})
    msg.mail_to.append(recipient)
    msg.send(smtp=dict(host=app.config["MAIL_SERVER"], port=app.config["MAIL_PORT"]))


def generate_confirmation_token(*args, **kwargs):
    """Generate Organisation registration confirmation token."""
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
    salt = app.config["SALT"]
    if len(kwargs) == 0:
        return serializer.dumps(args[0] if len(args) == 1 else args, salt=salt)
    else:
        return serializer.dumps(kwargs.values()[0] if len(kwargs) == 1 else kwargs, salt=salt)


# Token Expiry after 15 days.
def confirm_token(token, expiration=1300000, unsafe=False):
    """Genearate confirmaatin token."""
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
    if unsafe:
        data = serializer.loads_unsafe(token)
    else:
        data = serializer.loads(token, salt=app.config["SALT"], max_age=expiration)
    return data


def append_qs(url, **qs):
    """Append new query strings to an arbitraty URL."""
    return url + ('&' if urlparse(url).query else '?') + urlencode(qs, doseq=True)


def track_event(category, action, label=None, value=0):
    """Track application events with Google Analytics."""
    ga_tracking_id = app.config.get("GA_TRACKING_ID")
    if not ga_tracking_id:
        return

    data = {
        "v": "1",  # API Version.
        "tid": ga_tracking_id,  # Tracking ID / Property ID.
        # Anonymous Client Identifier. Ideally, this should be a UUID that
        # is associated with particular user, device, or browser instance.
        "cid": current_user.uuid,
        "t": "event",  # Event hit type.
        "ec": category,  # Event category.
        "ea": action,  # Event action.
        "el": label,  # Event label.
        "ev": value,  # Event value, must be an integer
    }

    response = requests.post("http://www.google-analytics.com/collect", data=data)

    # If the request fails, this will raise a RequestException. Depending
    # on your application's needs, this may be a non-error and can be caught
    # by the caller.
    response.raise_for_status()
    # Returning response only for test, but can be used in application for some other reasons
    return response


def set_server_name():
    """Set the server name for batch processes."""
    if not app.config.get("SERVER_NAME"):
        if EXTERNAL_SP:
            app.config["SERVER_NAME"] = "127.0.0.1:5000"
        else:
            app.config[
                "SERVER_NAME"] = "orcidhub.org.nz" if ENV == "prod" else ENV + ".orcidhub.org.nz"


def send_work_funding_invitation(inviter, org, email, name, task_id=None, invitation_template=None, **kwargs):
    """Send a work or funding invitation to join ORCID Hub logging in via ORCID."""
    try:
        logger.info(f"*** Sending an invitation to '{name} <{email}>' "
                    f"submitted by {inviter} of {org}")

        email = email.lower()
        user, user_created = User.get_or_create(email=email)
        if user_created:
            user.name = name
            user.created_by = inviter.id
        else:
            user.updated_by = inviter.id

        user.organisation = org
        user.roles |= Role.RESEARCHER

        token = generate_confirmation_token(email=email, org=org.name)
        with app.app_context():
            url = flask.url_for('orcid_login', invitation_token=token, _external=True)
            invitation_url = flask.url_for(
                "short_url", short_id=Url.shorten(url).short_id, _external=True)
            send_email(
                invitation_template,
                recipient=(user.organisation.name, user.email),
                reply_to=(inviter.name, inviter.email),
                invitation_url=invitation_url,
                org_name=user.organisation.name,
                org=org,
                user=user)

        user.save()

        user_org, user_org_created = UserOrg.get_or_create(user=user, org=org)
        if user_org_created:
            user_org.created_by = inviter.id
        else:
            user_org.updated_by = inviter.id
        user_org.affiliations = 0
        user_org.save()

        ui = UserInvitation.create(
            task_id=task_id,
            invitee_id=user.id,
            inviter_id=inviter.id,
            org=org,
            email=email,
            first_name=name,
            affiliations=0,
            organisation=org.name,
            disambiguated_id=org.disambiguated_id,
            disambiguation_source=org.disambiguation_source,
            token=token)

        return ui

    except Exception as ex:
        logger.error(f"Exception occured while sending mails {ex}")
        raise ex


def create_or_update_work(user, org_id, records, *args, **kwargs):
    """Create or update work record of a user."""
    records = list(unique_everseen(records, key=lambda t: t.work_record.id))
    org = Organisation.get(id=org_id)
    client_id = org.orcid_client_id
    api = orcid_client.MemberAPI(org, user)

    profile_record = api.get_record()

    if profile_record:
        activities = profile_record.get("activities-summary")

        def is_org_rec(rec):
            return (rec.get("source").get("source-client-id")
                    and rec.get("source").get("source-client-id").get("path") == client_id)

        works = []

        for r in activities.get("works").get("group"):
            ws = r.get("work-summary")[0]
            if is_org_rec(ws):
                works.append(ws)

        taken_put_codes = {
            r.work_record.work_invitees.put_code
            for r in records if r.work_record.work_invitees.put_code
        }

        def match_put_code(records, work_record, work_invitees):
            """Match and assign put-code to a single work record and the existing ORCID records."""
            if work_invitees.put_code:
                return
            for r in records:
                put_code = r.get("put-code")
                if put_code in taken_put_codes:
                    continue

                if ((r.get("title") is None and r.get("title").get("title") is None
                     and r.get("title").get("title").get("value") is None and r.get("type") is None)
                        or (r.get("title").get("title").get("value") == work_record.title
                            and r.get("type") == work_record.type)):
                    work_invitees.put_code = put_code
                    work_invitees.save()
                    taken_put_codes.add(put_code)
                    app.logger.debug(
                        f"put-code {put_code} was asigned to the work record "
                        f"(ID: {work_record.id}, Task ID: {work_record.task_id})")
                    break

        for task_by_user in records:
            wr = task_by_user.work_record
            wi = task_by_user.work_record.work_invitees
            match_put_code(works, wr, wi)

        for task_by_user in records:
            wi = task_by_user.work_record.work_invitees

            try:
                put_code, orcid, created = api.create_or_update_work(task_by_user)
                if created:
                    wi.add_status_line(f"Work record was created.")
                else:
                    wi.add_status_line(f"Work record was updated.")
                wi.orcid = orcid
                wi.put_code = put_code

            except Exception as ex:
                logger.exception(f"For {user} encountered exception")
                exception_msg = ""
                if ex and ex.body:
                    exception_msg = json.loads(ex.body)
                wi.add_status_line(f"Exception occured processing the record: {exception_msg}.")
                wr.add_status_line(
                    f"Error processing record. Fix and reset to enable this record to be processed: {exception_msg}."
                )

            finally:
                wi.processed_at = datetime.utcnow()
                wr.save()
                wi.save()
    else:
        # TODO: Invitation resend in case user revokes organisation permissions
        app.logger.debug(f"Should resend an invite to the researcher asking for permissions")
        return


def create_or_update_funding(user, org_id, records, *args, **kwargs):
    """Create or update funding record of a user."""
    records = list(unique_everseen(records, key=lambda t: t.funding_record.id))
    org = Organisation.get(id=org_id)
    client_id = org.orcid_client_id
    api = orcid_client.MemberAPI(org, user)

    profile_record = api.get_record()

    if profile_record:
        activities = profile_record.get("activities-summary")

        def is_org_rec(rec):
            return (rec.get("source").get("source-client-id")
                    and rec.get("source").get("source-client-id").get("path") == client_id)

        fundings = []

        for r in activities.get("fundings").get("group"):
            fs = r.get("funding-summary")[0]
            if is_org_rec(fs):
                fundings.append(fs)

        taken_put_codes = {
            r.funding_record.funding_invitees.put_code
            for r in records if r.funding_record.funding_invitees.put_code
        }

        def match_put_code(records, funding_record, funding_invitees):
            """Match and asign put-code to a single funding record and the existing ORCID records."""
            if funding_invitees.put_code:
                return
            for r in records:
                put_code = r.get("put-code")
                if put_code in taken_put_codes:
                    continue

                if ((r.get("title") is None and r.get("title").get("title") is None
                     and r.get("title").get("title").get("value") is None and r.get("type") is None
                     and r.get("organization") is None
                     and r.get("organization").get("name") is None)
                        or (r.get("title").get("title").get("value") == funding_record.title
                            and r.get("type") == funding_record.type
                            and r.get("organization").get("name") == funding_record.org_name)):
                    funding_invitees.put_code = put_code
                    funding_invitees.save()
                    taken_put_codes.add(put_code)
                    app.logger.debug(
                        f"put-code {put_code} was asigned to the funding record "
                        f"(ID: {funding_record.id}, Task ID: {funding_record.task_id})")
                    break

        for task_by_user in records:
            fr = task_by_user.funding_record
            fi = task_by_user.funding_record.funding_invitees
            match_put_code(fundings, fr, fi)

        for task_by_user in records:
            fi = task_by_user.funding_record.funding_invitees

            try:
                put_code, orcid, created = api.create_or_update_funding(task_by_user)
                if created:
                    fi.add_status_line(f"Funding record was created.")
                else:
                    fi.add_status_line(f"Funding record was updated.")
                fi.orcid = orcid
                fi.put_code = put_code

            except Exception as ex:
                logger.exception(f"For {user} encountered exception")
                exception_msg = ""
                if ex and ex.body:
                    exception_msg = json.loads(ex.body)
                fi.add_status_line(f"Exception occured processing the record: {exception_msg}.")
                fr.add_status_line(
                    f"Error processing record. Fix and reset to enable this record to be processed: {exception_msg}."
                )

            finally:
                fi.processed_at = datetime.utcnow()
                fr.save()
                fi.save()
    else:
        # TODO: Invitation resend in case user revokes organisation permissions
        app.logger.debug(f"Should resend an invite to the researcher asking for permissions")
        return


def send_user_invitation(inviter,
                         org,
                         email,
                         first_name,
                         last_name,
                         affiliation_types=None,
                         orcid=None,
                         department=None,
                         organisation=None,
                         city=None,
                         state=None,
                         country=None,
                         course_or_role=None,
                         start_date=None,
                         end_date=None,
                         affiliations=None,
                         disambiguated_id=None,
                         disambiguation_source=None,
                         task_id=None,
                         cc_email=None,
                         **kwargs):
    """Send an invitation to join ORCID Hub logging in via ORCID."""
    try:
        logger.info(f"*** Sending an invitation to '{first_name} {last_name} <{email}>' "
                    f"submitted by {inviter} of {org} for affiliations: {affiliation_types}")

        email = email.lower()
        user, user_created = User.get_or_create(email=email)
        if user_created:
            user.first_name = first_name
            user.last_name = last_name
        user.organisation = org
        user.roles |= Role.RESEARCHER

        token = generate_confirmation_token(email=email, org=org.name)
        with app.app_context():
            url = flask.url_for('orcid_login', invitation_token=token, _external=True)
            invitation_url = flask.url_for(
                "short_url", short_id=Url.shorten(url).short_id, _external=True)
            send_email(
                "email/researcher_invitation.html",
                recipient=(user.organisation.name, user.email),
                reply_to=(inviter.name, inviter.email),
                cc_email=cc_email,
                invitation_url=invitation_url,
                org_name=user.organisation.name,
                org=org,
                user=user)

        user.save()

        user_org, user_org_created = UserOrg.get_or_create(user=user, org=org)
        if user_org_created:
            user_org.created_by = inviter.id
        else:
            user_org.updated_by = inviter.id

        if affiliations is None and affiliation_types:
            affiliations = 0
            if affiliation_types & {"faculty", "staff"}:
                affiliations = Affiliation.EMP
            if affiliation_types & {"student", "alum"}:
                affiliations |= Affiliation.EDU
        user_org.affiliations = affiliations

        user_org.save()
        ui = UserInvitation.create(
            task_id=task_id,
            invitee_id=user.id,
            inviter_id=inviter.id,
            org=org,
            email=email,
            first_name=first_name,
            last_name=last_name,
            orcid=orcid,
            department=department,
            organisation=org.name,
            city=city,
            state=state,
            country=country,
            course_or_role=course_or_role,
            start_date=start_date,
            end_date=end_date,
            affiliations=affiliations,
            disambiguated_id=disambiguated_id,
            disambiguation_source=disambiguation_source,
            token=token)

        status = "The invitation sent at " + datetime.utcnow().isoformat(timespec="seconds")
        (AffiliationRecord.update(status=AffiliationRecord.status + "\n" + status).where(
            AffiliationRecord.status.is_null(False), AffiliationRecord.email == email).execute())
        (AffiliationRecord.update(status=status).where(AffiliationRecord.status.is_null(),
                                                       AffiliationRecord.email == email).execute())
        return ui

    except Exception as ex:
        logger.exception(f"Exception occured while sending mails {ex}")
        raise


def unique_everseen(iterable, key=None):
    """List unique elements, preserving order. Remember all elements ever seen.

    The snippet is taken form https://docs.python.org/3.6/library/itertools.html#itertools-recipes
    >>> unique_everseen('AAAABBBCCDAABBB')
    A B C D
    >>> unique_everseen('ABBCcAD', str.lower)
    A B C D
    """
    seen = set()
    seen_add = seen.add
    if key is None:
        for element in filterfalse(seen.__contains__, iterable):
            seen_add(element)
            yield element
    else:
        for element in iterable:
            k = key(element)
            if k not in seen:
                seen_add(k)
                yield element


def create_or_update_affiliations(user, org_id, records, *args, **kwargs):
    """Create or update affiliation record of a user.

    1. Retries user edurcation and employment surramy from ORCID;
    2. Match the recodrs with the summary;
    3. If there is match update the record;
    4. If no match create a new one.
    """
    records = list(unique_everseen(records, key=lambda t: t.affiliation_record.id))
    org = Organisation.get(id=org_id)
    client_id = org.orcid_client_id
    api = orcid_client.MemberAPI(org, user)
    profile_record = api.get_record()
    if profile_record:
        activities = profile_record.get("activities-summary")

        def is_org_rec(rec):
            return (rec.get("source").get("source-client-id")
                    and rec.get("source").get("source-client-id").get("path") == client_id)

        employments = [
            r for r in (activities.get("employments").get("employment-summary")) if is_org_rec(r)
        ]
        educations = [
            r for r in (activities.get("educations").get("education-summary")) if is_org_rec(r)
        ]

        taken_put_codes = {
            r.affiliation_record.put_code
            for r in records if r.affiliation_record.put_code
        }

        def match_put_code(records, affiliation_record):
            """Match and asign put-code to a single affiliation record and the existing ORCID records."""
            if affiliation_record.put_code:
                return
            for r in records:
                put_code = r.get("put-code")
                if put_code in taken_put_codes:
                    continue

                if ((r.get("start-date") is None and r.get("end-date") is None
                     and r.get("department-name") is None and r.get("role-title") is None)
                        or (r.get("start-date") == affiliation_record.start_date
                            and r.get("department-name") == affiliation_record.department
                            and r.get("role-title") == affiliation_record.role)):
                    affiliation_record.put_code = put_code
                    taken_put_codes.add(put_code)
                    app.logger.debug(
                        f"put-code {put_code} was asigned to the affiliation record "
                        f"(ID: {affiliation_record.id}, Task ID: {affiliation_record.task_id})")
                    break

        for task_by_user in records:
            try:
                ar = task_by_user.affiliation_record
                at = ar.affiliation_type.lower()

                if at in EMP_CODES:
                    match_put_code(employments, ar)
                    affiliation = Affiliation.EMP
                elif at in EDU_CODES:
                    match_put_code(educations, ar)
                    affiliation = Affiliation.EDU
                else:
                    logger.info(f"For {user} not able to determine affiliaton type with {org}")
                    ar.processed_at = datetime.utcnow()
                    ar.add_status_line(
                        f"Unsupported affiliation type '{at}' allowed values are: " + ', '.join(
                            at for at in AFFILIATION_TYPES))
                    ar.save()
                    continue

                put_code, orcid, created = api.create_or_update_affiliation(
                    affiliation=affiliation, **ar._data)
                if created:
                    ar.add_status_line(f"{str(affiliation)} record was created.")
                else:
                    ar.add_status_line(f"{str(affiliation)} record was updated.")
                ar.orcid = orcid
                ar.put_code = put_code
                ar.processed_at = datetime.utcnow()

            except Exception as ex:
                logger.exception(f"For {user} encountered exception")
                ar.add_status_line(f"Exception occured processing the record: {ex}.")
                ar.processed_at = datetime.utcnow()

            finally:
                ar.save()
    else:
        for task_by_user in records:
            user = User.get(
                email=task_by_user.affiliation_record.email, organisation=task_by_user.org)
            user_org = UserOrg.get(user=user, org=task_by_user.org)
            token = generate_confirmation_token(email=user.email, org=org.name)
            with app.app_context():
                url = flask.url_for('orcid_login', invitation_token=token, _external=True)
                invitation_url = flask.url_for(
                    "short_url", short_id=Url.shorten(url).short_id, _external=True)
                send_email(
                    "email/researcher_reinvitation.html",
                    recipient=(user.organisation.name, user.email),
                    reply_to=(task_by_user.created_by.name, task_by_user.created_by.email),
                    invitation_url=invitation_url,
                    org_name=user.organisation.name,
                    org=org,
                    user=user)
            UserInvitation.create(
                invitee_id=user.id,
                inviter_id=task_by_user.created_by.id,
                org=org,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                orcid=user.orcid,
                organisation=org.name,
                city=org.city,
                state=org.state,
                country=org.country,
                start_date=task_by_user.affiliation_record.start_date,
                end_date=task_by_user.affiliation_record.end_date,
                affiliations=user_org.affiliations,
                disambiguated_id=org.disambiguated_id,
                disambiguation_source=org.disambiguation_source,
                token=token)

            status = "Exception occured while accessing user's profile. " \
                     "Hence, The invitation resent at " + datetime.utcnow().isoformat(timespec="seconds")
            (AffiliationRecord.update(status=AffiliationRecord.status + "\n" + status).where(
                AffiliationRecord.status.is_null(False),
                AffiliationRecord.email == user.email).execute())
            (AffiliationRecord.update(status=status).where(
                AffiliationRecord.status.is_null(),
                AffiliationRecord.email == user.email).execute())
            return


def process_work_records(max_rows=20):
    """Process uploaded work records."""
    set_server_name()
    task_ids = set()
    work_ids = set()
    """This query is to retrieve Tasks associated with work records, which are not processed but are active"""

    tasks = (Task.select(
        Task, WorkRecord, WorkInvitees,
        User, UserInvitation.id.alias("invitation_id"), OrcidToken).where(
            WorkRecord.processed_at.is_null(), WorkInvitees.processed_at.is_null(),
            WorkRecord.is_active,
            (OrcidToken.id.is_null(False) |
             ((WorkInvitees.status.is_null()) |
              (WorkInvitees.status.contains("sent").__invert__())))).join(
                  WorkRecord, on=(Task.id == WorkRecord.task_id)).join(
                      WorkInvitees,
                      on=(WorkRecord.id == WorkInvitees.work_record_id)).join(
                          User, JOIN.LEFT_OUTER,
                          on=((User.email == WorkInvitees.email) | (User.orcid == WorkInvitees.orcid)))
             .join(Organisation, JOIN.LEFT_OUTER, on=(Organisation.id == Task.org_id)).join(
                 UserInvitation,
                 JOIN.LEFT_OUTER,
                 on=((UserInvitation.email == WorkInvitees.email)
                     & (UserInvitation.task_id == Task.id))).join(
                         OrcidToken,
                         JOIN.LEFT_OUTER,
                         on=((OrcidToken.user_id == User.id)
                             & (OrcidToken.org_id == Organisation.id)
                             & (OrcidToken.scope.contains("/activities/update")))).limit(max_rows))

    for (task_id, org_id, work_record_id, user), tasks_by_user in groupby(tasks, lambda t: (
            t.id,
            t.org_id,
            t.work_record.id,
            t.work_record.work_invitees.user,)):
        """If we have the token associated to the user then update the work record, otherwise send him an invite"""
        if (user.id is None or user.orcid is None or not OrcidToken.select().where(
            (OrcidToken.user_id == user.id) & (OrcidToken.org_id == org_id) &
            (OrcidToken.scope.contains("/activities/update"))).exists()):  # noqa: E127, E129

            for k, tasks in groupby(
                    tasks_by_user,
                    lambda t: (
                        t.created_by,
                        t.org,
                        t.work_record.work_invitees.email,
                        t.work_record.work_invitees.first_name, )
            ):  # noqa: E501
                send_work_funding_invitation(*k, task_id=task_id, invitation_template="email/work_invitation.html")
                with db.atomic():
                    status = "The invitation sent at " + datetime.utcnow().isoformat(timespec="seconds")
                    (WorkInvitees.update(status=WorkInvitees.status + "\n" + status).where(
                        WorkInvitees.status.is_null(False), WorkInvitees.email == k[2]).execute())
                    (WorkInvitees.update(status=status).where(
                        WorkInvitees.status.is_null(), WorkInvitees.email == k[2]).execute())

        else:
            create_or_update_work(user, org_id, tasks_by_user)
        task_ids.add(task_id)
        work_ids.add(work_record_id)

    for work_record in WorkRecord.select().where(WorkRecord.id << work_ids):
        # The Work record is processed for all invitees
        if not (WorkInvitees.select().where(
                WorkInvitees.work_record_id == work_record.id,
                WorkInvitees.processed_at.is_null()).exists()):
            work_record.processed_at = datetime.utcnow()
            if not work_record.status or "error" not in work_record.status:
                work_record.add_status_line("Work record is processed.")
            work_record.save()

    for task in Task.select().where(Task.id << task_ids):
        # The task is completed (Once all records are processed):
        if not (WorkRecord.select().where(WorkRecord.task_id == task.id, WorkRecord.processed_at.is_null()).exists()):
            task.completed_at = datetime.utcnow()
            task.save()
            error_count = WorkRecord.select().where(
                WorkRecord.task_id == task.id, WorkRecord.status**"%error%").count()
            row_count = task.work_record_count

            with app.app_context():
                protocol_scheme = 'http'
                if not EXTERNAL_SP:
                    protocol_scheme = 'https'
                export_url = flask.url_for(
                    "workrecord.export",
                    export_type="json",
                    _scheme=protocol_scheme,
                    task_id=task.id,
                    _external=True)
                send_email(
                    "email/work_task_completed.html",
                    subject="Work Process Update",
                    recipient=(task.created_by.name, task.created_by.email),
                    error_count=error_count,
                    row_count=row_count,
                    export_url=export_url,
                    filename=task.filename)


def process_funding_records(max_rows=20):
    """Process uploaded affiliation records."""
    set_server_name()
    task_ids = set()
    funding_ids = set()
    """This query is to retrieve Tasks associated with funding records, which are not processed but are active"""
    tasks = (Task.select(
        Task, FundingRecord, FundingInvitees,
        User, UserInvitation.id.alias("invitation_id"), OrcidToken).where(
            FundingRecord.processed_at.is_null(), FundingInvitees.processed_at.is_null(),
            FundingRecord.is_active,
            (OrcidToken.id.is_null(False) |
             ((FundingInvitees.status.is_null()) |
              (FundingInvitees.status.contains("sent").__invert__())))).join(
                  FundingRecord, on=(Task.id == FundingRecord.task_id)).join(
                      FundingInvitees,
                      on=(FundingRecord.id == FundingInvitees.funding_record_id)).join(
                          User,
                          JOIN.LEFT_OUTER,
                          on=((User.email == FundingInvitees.email) |
                              (User.orcid == FundingInvitees.orcid)))
             .join(Organisation, JOIN.LEFT_OUTER, on=(Organisation.id == Task.org_id)).join(
                 UserInvitation,
                 JOIN.LEFT_OUTER,
                 on=((UserInvitation.email == FundingInvitees.email)
                     & (UserInvitation.task_id == Task.id))).join(
                         OrcidToken,
                         JOIN.LEFT_OUTER,
                         on=((OrcidToken.user_id == User.id)
                             & (OrcidToken.org_id == Organisation.id)
                             & (OrcidToken.scope.contains("/activities/update")))).limit(max_rows))

    for (task_id, org_id, funding_record_id, user), tasks_by_user in groupby(tasks, lambda t: (
            t.id,
            t.org_id,
            t.funding_record.id,
            t.funding_record.funding_invitees.user,)):
        """If we have the token associated to the user then update the funding record, otherwise send him an invite"""
        if (user.id is None or user.orcid is None or not OrcidToken.select().where(
            (OrcidToken.user_id == user.id) & (OrcidToken.org_id == org_id) &
            (OrcidToken.scope.contains("/activities/update"))).exists()):  # noqa: E127, E129

            for k, tasks in groupby(
                    tasks_by_user,
                    lambda t: (
                        t.created_by,
                        t.org,
                        t.funding_record.funding_invitees.email,
                        t.funding_record.funding_invitees.first_name, )
            ):  # noqa: E501
                send_work_funding_invitation(*k, task_id=task_id, invitation_template="email/funding_invitation.html")
                with db.atomic():
                    status = "The invitation sent at " + datetime.utcnow().isoformat(timespec="seconds")
                    (FundingInvitees.update(status=FundingInvitees.status + "\n" + status).where(
                        FundingInvitees.status.is_null(False), FundingInvitees.email == k[2]).execute())
                    (FundingInvitees.update(status=status).where(
                        FundingInvitees.status.is_null(), FundingInvitees.email == k[2]).execute())
        else:
            create_or_update_funding(user, org_id, tasks_by_user)
        task_ids.add(task_id)
        funding_ids.add(funding_record_id)

    for funding_record in FundingRecord.select().where(FundingRecord.id << funding_ids):
        # The funding record is processed for all invitees
        if not (FundingInvitees.select().where(
                FundingInvitees.funding_record_id == funding_record.id,
                FundingInvitees.processed_at.is_null()).exists()):
            funding_record.processed_at = datetime.utcnow()
            if not funding_record.status or "error" not in funding_record.status:
                funding_record.add_status_line("Funding record is processed.")
            funding_record.save()

    for task in Task.select().where(Task.id << task_ids):
        # The task is completed (Once all records are processed):
        if not (FundingRecord.select().where(FundingRecord.task_id == task.id,
                                             FundingRecord.processed_at.is_null()).exists()):
            task.completed_at = datetime.utcnow()
            task.save()
            error_count = FundingRecord.select().where(
                FundingRecord.task_id == task.id, FundingRecord.status**"%error%").count()
            row_count = task.record_funding_count

            with app.app_context():
                protocol_scheme = 'http'
                if not EXTERNAL_SP:
                    protocol_scheme = 'https'
                export_url = flask.url_for(
                    "fundingrecord.export",
                    export_type="json",
                    _scheme=protocol_scheme,
                    task_id=task.id,
                    _external=True)
                send_email(
                    "email/funding_task_completed.html",
                    subject="Funding Process Update",
                    recipient=(task.created_by.name, task.created_by.email),
                    error_count=error_count,
                    row_count=row_count,
                    export_url=export_url,
                    filename=task.filename)


def process_affiliation_records(max_rows=20):
    """Process uploaded affiliation records."""
    set_server_name()
    # TODO: optimize removing redundant fields
    # TODO: perhaps it should be broken into 2 queries
    task_ids = set()
    tasks = (Task.select(
        Task, AffiliationRecord, User, UserInvitation.id.alias("invitation_id"), OrcidToken).where(
            AffiliationRecord.processed_at.is_null(), AffiliationRecord.is_active,
            ((User.id.is_null(False) & User.orcid.is_null(False) & OrcidToken.id.is_null(False)) |
             ((User.id.is_null() | User.orcid.is_null() | OrcidToken.id.is_null()) &
              UserInvitation.id.is_null() &
              (AffiliationRecord.status.is_null()
               | AffiliationRecord.status.contains("sent").__invert__())))).join(
                   AffiliationRecord, on=(Task.id == AffiliationRecord.task_id)).join(
                       User,
                       JOIN.LEFT_OUTER,
                       on=((User.email == AffiliationRecord.email) |
                           (User.orcid == AffiliationRecord.orcid))).join(
                               Organisation, JOIN.LEFT_OUTER, on=(Organisation.id == Task.org_id))
             .join(
                 UserInvitation,
                 JOIN.LEFT_OUTER,
                 on=((UserInvitation.email == AffiliationRecord.email) &
                     (UserInvitation.task_id == Task.id))).join(
                         OrcidToken,
                         JOIN.LEFT_OUTER,
                         on=((OrcidToken.user_id == User.id) &
                             (OrcidToken.org_id == Organisation.id) &
                             (OrcidToken.scope.contains("/activities/update")))).limit(max_rows))
    for (task_id, org_id, user), tasks_by_user in groupby(tasks, lambda t: (
            t.id,
            t.org_id,
            t.affiliation_record.user, )):
        if (user.id is None or user.orcid is None or not OrcidToken.select().where(
            (OrcidToken.user_id == user.id) & (OrcidToken.org_id == org_id) &
            (OrcidToken.scope.contains("/activities/update"))).exists()):  # noqa: E127, E129

            # maps invitation attributes to affiliation type set:
            # - the user who uploaded the task;
            # - the user organisation;
            # - the invitee email;
            # - the invitee first_name;
            # - the invitee last_name
            invitation_dict = {
                k: set(t.affiliation_record.affiliation_type.lower() for t in tasks)
                for k, tasks in groupby(
                    tasks_by_user,
                    lambda t: (t.created_by, t.org, t.affiliation_record.email, t.affiliation_record.first_name, t.affiliation_record.last_name)  # noqa: E501
                )  # noqa: E501
            }
            for invitation, affiliations in invitation_dict.items():
                try:
                    send_user_invitation(*invitation, affiliations, task_id=task_id)
                except Exception as ex:
                    email = invitation[2]
                    (AffiliationRecord.update(
                        processed_at=datetime.utcnow(), status=f"Failed to send an invitation: {ex}.")
                     .where(AffiliationRecord.task_id == task_id, AffiliationRecord.email == email,
                            AffiliationRecord.processed_at.is_null())).execute()

        else:  # user exits and we have tokens
            create_or_update_affiliations(user, org_id, tasks_by_user)
        task_ids.add(task_id)
    for task in Task.select().where(Task.id << task_ids):
        # The task is completed (all recores are processed):
        if not (AffiliationRecord.select().where(
                AffiliationRecord.task_id == task.id,
                AffiliationRecord.processed_at.is_null()).exists()):
            task.completed_at = datetime.utcnow()
            task.save()
            error_count = AffiliationRecord.select().where(
                AffiliationRecord.task_id == task.id, AffiliationRecord.status**"%error%").count()
            row_count = task.record_count
            orcid_rec_count = task.affiliationrecord_set.select(
                AffiliationRecord.orcid).distinct().count()

            with app.app_context():
                protocol_scheme = 'http'
                if not EXTERNAL_SP:
                    protocol_scheme = 'https'
                export_url = flask.url_for(
                    "affiliationrecord.export",
                    export_type="csv",
                    _scheme=protocol_scheme,
                    task_id=task.id,
                    _external=True)
                try:
                    send_email(
                        "email/task_completed.html",
                        subject="Affiliation Process Update",
                        recipient=(task.created_by.name, task.created_by.email),
                        error_count=error_count,
                        row_count=row_count,
                        orcid_rec_count=orcid_rec_count,
                        export_url=export_url,
                        filename=task.filename)
                except Exception as ex:
                    logger.exception(
                        "Failed to send batch process comletion notification message.")


def process_tasks(max_rows=20):
    """Handle batch task expiration.

    Send a information messages about upcoming removal of the processed/uploaded tasks
    based on date whichever is greater either created_at + month or updated_at + 2 weeks
    and removal of expired tasks based on the expiry date.

    Args:
        max_rows (int): The maximum number of rows that will get processed in one go.

    Returns:
        int. The number of processed task records.

    """
    Task.delete().where((Task.expires_at < datetime.utcnow())).execute()

    for task in Task.select().where(Task.expires_at.is_null()).limit(max_rows):

        max_created_at_expiry = (task.created_at + timedelta(weeks=4))
        max_updated_at_expiry = (task.updated_at + timedelta(weeks=2))

        max_expiry_date = max_created_at_expiry

        if max_created_at_expiry < max_updated_at_expiry:
            max_expiry_date = max_updated_at_expiry

        if max_expiry_date < (datetime.now() + timedelta(weeks=1)):
            task.expires_at = max_expiry_date
            task.save()
            if task.task_type == TaskType.AFFILIATION.value:
                error_count = AffiliationRecord.select().where(
                    AffiliationRecord.task_id == task.id, AffiliationRecord.status ** "%error%").count()
            elif task.task_type == TaskType.FUNDING.value:
                error_count = FundingRecord.select().where(FundingRecord.task_id == task.id,
                                                           FundingRecord.status ** "%error%").count()
            else:
                raise Exception(f"Unexpeced task type: {task.task_type} ({task}).")

            with app.app_context():
                protocol_scheme = 'http'
                if not EXTERNAL_SP:
                    protocol_scheme = 'https'
                export_url = flask.url_for(
                    "affiliationrecord.export"
                    if task.task_type == TaskType.AFFILIATION.value else "fundingrecord.export",
                    export_type="csv",
                    _scheme=protocol_scheme,
                    task_id=task.id,
                    _external=True)
                send_email(
                    "email/task_expiration.html",
                    task=task,
                    subject="Batch process task is about to expire",
                    recipient=(task.created_by.name, task.created_by.email),
                    error_count=error_count,
                    export_url=export_url)


def get_client_credentials_token(org, scope="/webhook"):
    """Request a cient credetials grant type access token and store it.

    The any previously requesed with the give scope tokens will be deleted.
    """
    resp = requests.post(
        app.config["TOKEN_URL"],
        headers={"Accepts": "application/json"},
        data=dict(
            client_id=org.orcid_client_id,
            client_secret=org.orcid_secret,
            scope=scope,
            grant_type="client_credentials"))
    OrcidToken.delete().where(OrcidToken.org == org, OrcidToken.scope == "/webhook").execute()
    data = resp.json()
    token = OrcidToken.create(
        org=org,
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        scope=data.get("scope") or scope,
        expires_in=data["expires_in"])
    return token


def register_orcid_webhook(user, callback_url=None):
    """Register an ORCID webhook for the given user profile update events.

    If URL is given, it will be used for as call-back URL.
    """
    set_server_name()
    try:
        token = OrcidToken.get(org=user.organisation, scope="/webhook")
    except OrcidToken.DoesNotExist:
        token = get_client_credentials_token(org=user.organisation, scope="/webhook")
    if callback_url in None:
        with app.app_context():
            callback_url = quote(url_for("update_webhook", user_id=user.id))
    elif '/' in callback_url:
        callback_url = quote(callback_url)
    url = f"{app.config['TOKEN_URL']}/{user.orcid}/webhook/{callback_url}"
    resp = requests.put(
        url,
        headers={
            "Accepts": "application/json",
            "Authorization": f"Bearer {token.access_token}",
            "Content-Length": "0"
        })
    return resp
