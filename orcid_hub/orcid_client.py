# -*- coding: utf-8 -*-
"""
Swagger generated client 'monkey-patch' for logging API requests.

isort:skip_file
"""

from .config import ORCID_API_BASE, SCOPE_READ_LIMITED, SCOPE_ACTIVITIES_UPDATE, ORCID_BASE_URL
from flask_login import current_user
from .models import (OrcidApiCall, Affiliation, OrcidToken, FundingContributor as FundingCont,
                     ExternalId as ExternalIdModel, WorkContributor as WorkCont, WorkExternalId, PeerReviewExternalId)
from orcid_api import (configuration, rest, api_client, MemberAPIV20Api, SourceClientId, Source,
                       OrganizationAddress, DisambiguatedOrganization, Employment, Education,
                       Organization)
from orcid_api.rest import ApiException
from time import time
from urllib.parse import urlparse
from . import app
import json

url = urlparse(ORCID_API_BASE)
configuration.host = url.scheme + "://" + url.hostname


class OrcidRESTClientObject(rest.RESTClientObject):
    """REST Client with call logging."""

    def request(self,
                method,
                url,
                query_params=None,
                headers=None,
                body=None,
                post_params=None,
                _preload_content=True,
                _request_timeout=None,
                **kwargs):
        """Exectue REST API request and logs both request, response and the restponse time."""
        request_time = time()
        put_code = body.get("put-code") if body else None
        try:
            oac = OrcidApiCall.create(
                user_id=current_user.id if current_user else None,
                method=method,
                url=url,
                query_params=query_params,
                body=body,
                put_code=put_code)
        except Exception:
            app.logger.exception("Failed to create API call log entry.")
            oac = None
        res = super().request(
            method=method,
            url=url,
            query_params=query_params,
            headers=headers,
            body=body,
            post_params=post_params,
            _preload_content=_preload_content,
            _request_timeout=_request_timeout,
            **kwargs)
        if res and oac:
            oac.status = res.status
            oac.response_time_ms = round((time() - request_time) * 1000)
            if res.data:
                oac.response = res.data
            oac.save()

        return res


class MemberAPI(MemberAPIV20Api):
    """ORCID Mmeber API extension."""

    def __init__(self, org=None, user=None, access_token=None, *args, **kwargs):
        """Set up the configuration with the access token given to the org. by the user."""
        super().__init__(*args, **kwargs)
        self.set_config(org, user, access_token)

    def set_config(self, org=None, user=None, access_token=None):
        """Set up clietn configuration."""
        # global configuration
        if org is None:
            org = user.organisation
        self.org = org
        self.user = user
        if access_token is None:
            try:
                orcid_token = OrcidToken.get(
                    user_id=user.id,
                    org_id=org.id,
                    scope=SCOPE_READ_LIMITED[0] + "," + SCOPE_ACTIVITIES_UPDATE[0])
            except Exception:
                app.logger.exception("Exception occured while retriving ORCID Token")
                return None

            configuration.access_token = orcid_token.access_token
        else:
            configuration.access_token = access_token

        url = urlparse(ORCID_BASE_URL)
        self.source_clientid = SourceClientId(
            host=url.hostname,
            path=org.orcid_client_id,
            uri="http://" + url.hostname + "/client/" + org.orcid_client_id)

        self.source = Source(
            source_orcid=None, source_client_id=self.source_clientid, source_name=org.name)

    def get_record(self):
        """Fetch record details. (The generated one is broken)."""
        # import pdb; pdb.set_trace()
        header_params = {
            "Accept":
            self.api_client.select_header_content_type([
                'application/vnd.orcid+xml; qs=5', 'application/orcid+xml; qs=3',
                'application/xml', 'application/vnd.orcid+json; qs=4',
                'application/orcid+json; qs=2', 'application/json'
            ])
        }
        try:
            resp, code, headers = self.api_client.call_api(
                f"/v2.0/{self.user.orcid}",
                "GET",
                header_params=header_params,
                response_type=None,
                auth_settings=["orcid_auth"],
                _preload_content=False)
        except ApiException as ex:
            if ex.status == 401:
                try:
                    orcid_token = OrcidToken.get(
                        user_id=self.user.id,
                        org_id=self.org.id,
                        scope=SCOPE_READ_LIMITED[0] + "," + SCOPE_ACTIVITIES_UPDATE[0])
                    orcid_token.delete_instance()
                except Exception:
                    app.logger.exception("Exception occured while retriving ORCID Token")
                    return None
            app.logger.error(f"ApiException Occured: {ex}")
            return None

        if code != 200:
            app.logger.error(f"Failed to retrieve ORDIC profile. Code: {code}.")
            app.logger.info(f"Headers: {headers}")
            app.logger.info(f"Body: {resp.data.decode()}")
            return None

        return json.loads(resp.data.decode())

    def is_emp_or_edu_record_present(self, affiliation_type):
        """Determine if there is already an affiliation record for the user.

        Returns:
            Either False or put-code, if there is an affiliation record.

        """
        try:
            if affiliation_type == Affiliation.EMP:
                resp = self.view_employments(self.user.orcid, _preload_content=False)
            else:
                resp = self.view_educations(self.user.orcid, _preload_content=False)

            if resp:
                data = json.loads(resp.data)
                records = data.get("employment-summary"
                                   if affiliation_type == Affiliation.EMP else "education-summary")
                for r in records:
                    if ("source-client-id" in r.get("source") and r.get("source").get("source-client-id") and
                            self.org.orcid_client_id == r.get("source").get("source-client-id").get("path")):
                        app.logger.info(f"For {self.user} there is {affiliation_type!s} "
                                        "present on ORCID profile.")
                        return r["put-code"]

        except ApiException as apiex:
            app.logger.error(
                f"For {self.user} while checking for employment and education records, Encountered Exception: {apiex}"
            )
            return False
        except Exception:
            app.logger.exception("Failed to verify presence of employment or education record.")
            return False
        return False

    def create_or_update_record_id_group(self, org=None, group_name=None, group_id=None, description=None,
                                         type=None, put_code=None):
        """Create or update group id record."""
        rec = GroupIdRecord()  # noqa: F405

        rec.name = group_name
        rec.group_id = group_id
        rec.description = description
        rec.type = type
        if put_code:
            rec.put_code = put_code

        try:
            api_call = self.update_group_id_record if put_code else self.create_group_id_record

            params = dict(body=rec, _preload_content=False)
            if put_code:
                params["put_code"] = put_code
            resp = api_call(**params)

            created = not bool(put_code)
            # retrieve the put-code from response Location header:
            if resp.status == 201:
                location = resp.headers.get("Location")
                try:
                    _, put_code = location.split("/")[-3::2]
                    put_code = int(put_code)
                except:
                    app.logger.exception("Failed to get put-code from the response.")
                    raise Exception("Failed to get put-code from the response.")
        except ApiException as ex:
            if ex.status == 404:
                app.logger.exception(
                    f"For {self.user} encountered exception, So updating related put_code")
            raise ex
        except:
            app.logger.exception(f"For {self.user} encountered exception")
        else:
            return (put_code, created)

    def create_or_update_peer_review(self, task_by_user, *args, **kwargs):
        """Create or update peer review record of a user."""
        pr = task_by_user.peer_review_record
        pi = pr.peer_review_invitee

        rec = PeerReview()    # noqa: F405

        # Source is an optional, so it does not matter whether we set that field in request or not.
        rec.source = self.source

        if pr.reviewer_role:
            rec.reviewer_role = pr.reviewer_role.upper()

        if pr.review_url:
            rec.review_url = Url(value=pr.review_url)  # noqa: F405

        if pr.review_type:
            rec.review_type = pr.review_type.upper()

        if pr.review_completion_date:
            rec.review_completion_date = pr.review_completion_date.as_orcid_dict()

        if pr.review_group_id:
            rec.review_group_id = pr.review_group_id

        if pr.subject_external_id_type and pr.subject_external_id_value:
            subject_external_id_relationship = None
            if pr.subject_external_id_relationship:
                subject_external_id_relationship = pr.subject_external_id_relationship.upper()
            subject_external_id_url = None
            if pr.subject_external_id_url:
                subject_external_id_url = Url(value=pr.subject_external_id_url)     # noqa: F405
            rec.subject_external_identifier = ExternalID(external_id_type=pr.subject_external_id_type,  # noqa: F405
                                                         external_id_value=pr.subject_external_id_value,
                                                         external_id_relationship=subject_external_id_relationship,
                                                         external_id_url=subject_external_id_url)

        if pr.subject_container_name:
            rec.subject_container_name = Title(value=pr.subject_container_name)     # noqa: F405

        if pr.subject_type:
            rec.subject_type = pr.subject_type.upper()

        if pr.subject_name_title:
            title = Title(value=pr.subject_name_title)  # noqa: F405
            subtitle = None
            if pr.subject_name_subtitle:
                subtitle = Subtitle(value=pr.subject_name_subtitle)  # noqa: F405
            translated_title = None
            if pr.subject_name_translated_title_lang_code and pr.subject_name_translated_title:
                translated_title = TranslatedTitle(value=pr.subject_name_translated_title,  # noqa: F405
                                                   language_code=pr.subject_name_translated_title_lang_code)
            rec.subject_name = WorkTitle(title=title, subtitle=subtitle,    # noqa: F405
                                         translated_title=translated_title)

        if pr.subject_url:
            rec.subject_url = Url(value=pr.subject_url)     # noqa: F405

        if pr.convening_org_name:
            address = None
            if pr.convening_org_city and pr.convening_org_country:
                region = None
                if pr.convening_org_region:
                    region = pr.convening_org_region
                address = OrganizationAddress(city=pr.convening_org_city, region=region,        # noqa: F405
                                              country=pr.convening_org_country)
            disambiguated_organization = None
            if pr.convening_org_disambiguated_identifier and pr.convening_org_disambiguation_source:
                disambiguated_organization = DisambiguatedOrganization(     # noqa: F405
                    disambiguated_organization_identifier=pr.convening_org_disambiguated_identifier,
                    disambiguation_source=pr.convening_org_disambiguation_source)
            rec.convening_organization = Organization(name=pr.convening_org_name, address=address,  # noqa: F405
                                                      disambiguated_organization=disambiguated_organization)

        put_code = pi.put_code
        if put_code:
            rec.put_code = pi.put_code

        if pi.visibility:
            rec.visibility = pi.visibility

        external_id_list = []
        external_ids = PeerReviewExternalId.select().where(PeerReviewExternalId.peer_review_record_id == pr.id)

        for exi in external_ids:
            external_id_type = exi.type
            external_id_value = exi.value
            external_id_url = None
            if exi.url:
                external_id_url = Url(value=exi.url)  # noqa: F405
            # Setting the external id relationship as 'SELF' by default, it can be either SELF/PART_OF
            external_id_relationship = exi.relationship.upper() if exi.relationship else "SELF"
            external_id_list.append(
                ExternalID(  # noqa: F405
                    external_id_type=external_id_type,
                    external_id_value=external_id_value,
                    external_id_url=external_id_url,
                    external_id_relationship=external_id_relationship))

        rec.review_identifiers = ExternalIDs(external_id=external_id_list)  # noqa: F405

        try:
            api_call = self.update_peer_review if put_code else self.create_peer_review

            params = dict(orcid=self.user.orcid, body=rec, _preload_content=False)
            if put_code:
                params["put_code"] = put_code
            resp = api_call(**params)
            app.logger.info(
                f"For {self.user} the ORCID record was {'updated' if put_code else 'created'} from {self.org}"
            )
            created = not bool(put_code)
            # retrieve the put-code from response Location header:
            if resp.status == 201:
                location = resp.headers.get("Location")
                try:
                    orcid, put_code = location.split("/")[-3::2]
                    put_code = int(put_code)
                    pi.put_code = put_code
                    pi.save()
                except:
                    app.logger.exception("Failed to get ORCID iD/put-code from the response.")
                    raise Exception("Failed to get ORCID iD/put-code from the response.")
            elif resp.status == 200:
                orcid = self.user.orcid

        except ApiException as ex:
            if ex.status == 404:
                pi.put_code = None
                pi.save()
                app.logger.exception(
                    f"For {self.user} encountered exception, So updating related put_code")
            raise ex
        except:
            app.logger.exception(f"For {self.user} encountered exception")
        else:
            return (put_code, orcid, created)

    def create_or_update_work(self, task_by_user, *args, **kwargs):
        """Create or update work record of a user."""
        wr = task_by_user.work_record
        wi = task_by_user.work_record.work_invitees

        rec = Work()    # noqa: F405
        title = None
        if wr.title:
            title = Title(value=wr.title)  # noqa: F405
        subtitle = None
        if wr.sub_title:
            subtitle = Subtitle(value=wr.sub_title)     # noqa: F405
        translated_title = None
        if wr.translated_title and wr.translated_title_language_code:
            translated_title = TranslatedTitle(value=wr.translated_title,  # noqa: F405
                                               language_code=wr.translated_title_language_code)  # noqa: F405
        rec.title = WorkTitle(title=title, subtitle=subtitle, translated_title=translated_title)  # noqa: F405

        if wr.journal_title:
            rec.journal_title = Title(value=wr.journal_title)  # noqa: F405

        short_description = None
        if wr.short_description:
            short_description = wr.short_description
        rec.short_description = short_description

        rec.source = self.source

        work_type = None
        if wr.type:
            work_type = wr.type
        rec.type = work_type

        if wr.publication_date:
            publication_date = wr.publication_date.as_orcid_dict()
            if wr.publication_media_type:
                publication_date['media-type'] = wr.publication_media_type.upper()
            rec.publication_date = publication_date

        put_code = wi.put_code
        if put_code:
            rec.put_code = wi.put_code

        if wi.visibility:
            rec.visibility = wi.visibility

        if wr.language_code:
            rec.language_code = wr.language_code

        if wr.country:
            rec.country = Country(value=wr.country)  # noqa: F405

        if wr.url:
            rec.url = Url(value=wr.url)  # noqa: F405

        if wr.citation_type and wr.citation_value:
            rec.citation = Citation(citation_type=wr.citation_type, citation_value=wr.citation_value)  # noqa: F405

        work_contributors = WorkCont.select().where(WorkCont.work_record_id == wr.id).order_by(
            WorkCont.contributor_sequence)

        work_contributor_list = []
        for w in work_contributors:
            path = None
            credit_name = None
            contributor_orcid = None
            contributor_attributes = None
            contributor_email = None

            if w.orcid:
                path = w.orcid

            if path:
                url = urlparse(ORCID_BASE_URL)
                uri = "http://" + url.hostname + "/" + path
                host = url.hostname
                contributor_orcid = ContributorOrcid(uri=uri, path=path, host=host)  # noqa: F405

            if w.name:
                credit_name = CreditName(value=w.name)  # noqa: F405

            if w.email:
                contributor_email = ContributorEmail(value=w.email)  # noqa: F405

            if w.role and w.contributor_sequence:
                contributor_attributes = ContributorAttributes(  # noqa: F405
                    contributor_role=w.role.upper(), contributor_sequence=w.contributor_sequence)

            work_contributor_list.append(
                Contributor(  # noqa: F405
                    contributor_orcid=contributor_orcid,
                    credit_name=credit_name,
                    contributor_email=contributor_email,
                    contributor_attributes=contributor_attributes))

        rec.contributors = WorkContributors(contributor=work_contributor_list)  # noqa: F405

        external_id_list = []
        external_ids = WorkExternalId.select().where(WorkExternalId.work_record_id == wr.id)

        for exi in external_ids:
            external_id_type = exi.type
            external_id_value = exi.value
            external_id_url = None
            if exi.url:
                external_id_url = Url(value=exi.url)  # noqa: F405
            # Setting the external id relationship as 'SELF' by default, it can be either SELF/PART_OF
            external_id_relationship = exi.relationship.upper() if exi.relationship else "SELF"
            external_id_list.append(
                ExternalID(  # noqa: F405
                    external_id_type=external_id_type,
                    external_id_value=external_id_value,
                    external_id_url=external_id_url,
                    external_id_relationship=external_id_relationship))

        rec.external_ids = ExternalIDs(external_id=external_id_list)  # noqa: F405

        try:
            api_call = self.update_work if put_code else self.create_work

            params = dict(orcid=self.user.orcid, body=rec, _preload_content=False)
            if put_code:
                params["put_code"] = put_code
            resp = api_call(**params)
            app.logger.info(
                f"For {self.user} the ORCID record was {'updated' if put_code else 'created'} from {self.org}"
            )
            created = not bool(put_code)
            # retrieve the put-code from response Location header:
            if resp.status == 201:
                location = resp.headers.get("Location")
                try:
                    orcid, put_code = location.split("/")[-3::2]
                    put_code = int(put_code)
                    wi.put_code = put_code
                    wi.save()
                except:
                    app.logger.exception("Failed to get ORCID iD/put-code from the response.")
                    raise Exception("Failed to get ORCID iD/put-code from the response.")
            elif resp.status == 200:
                orcid = self.user.orcid

        except ApiException as ex:
            if ex.status == 404:
                wi.put_code = None
                wi.save()
                app.logger.exception(
                    f"For {self.user} encountered exception, So updating related put_code")
            raise ex
        except:
            app.logger.exception(f"For {self.user} encountered exception")
        else:
            return (put_code, orcid, created)

    def create_or_update_funding(self, task_by_user, *args, **kwargs):
        """Create or update funding record of a user."""
        fr = task_by_user.funding_record
        fi = task_by_user.funding_record.funding_invitees

        if not fr.title:
            title = None

        city = fr.city
        country = fr.country
        region = fr.region
        disambiguated_id = fr.disambiguated_org_identifier
        disambiguation_source = fr.disambiguation_source
        org_name = fr.org_name
        funding_type = fr.type

        put_code = fi.put_code

        if not city:
            city = None
        if not region:
            region = None
        if not self.org.state:
            self.org.state = None

        organisation_address = OrganizationAddress(
            city=city or self.org.city,
            country=country or self.org.country,
            region=region or self.org.state)

        disambiguated_organization_details = DisambiguatedOrganization(
            disambiguated_organization_identifier=disambiguated_id or self.org.disambiguated_id,
            disambiguation_source=disambiguation_source or self.org.disambiguation_source)
        rec = Funding()  # noqa: F405

        rec.organization = Organization(
            name=org_name or self.org.name,
            address=organisation_address,
            disambiguated_organization=disambiguated_organization_details)

        organization_defined_type = fr.organization_defined_type
        title = Title(value=fr.title)  # noqa: F405
        translated_title = None
        if fr.translated_title:
            translated_title = TranslatedTitle(  # noqa: F405
                value=fr.translated_title,  # noqa: F405
                language_code=fr.translated_title_language_code)  # noqa: F405
        short_description = fr.short_description
        amount = fr.amount
        currency_code = fr.currency
        start_date = fr.start_date
        end_date = fr.end_date

        rec.source = self.source
        rec.type = funding_type
        rec.organization_defined_type = organization_defined_type
        rec.title = FundingTitle(title=title, translated_title=translated_title)  # noqa: F405
        rec.short_description = short_description
        rec.amount = Amount(value=amount, currency_code=currency_code)  # noqa: F405

        if fi.visibility:
            rec.visibility = fi.visibility

        if put_code:
            rec.put_code = put_code

        if start_date:
            rec.start_date = start_date.as_orcid_dict()
        if end_date:
            rec.end_date = end_date.as_orcid_dict()

        funding_contributors = FundingCont.select().where(FundingCont.funding_record_id == fr.id).order_by(
            FundingCont.id)

        funding_contributor_list = []
        if funding_contributors:
            for f in funding_contributors:
                path = None
                uri = None
                host = None
                credit_name = None
                contributor_email = None
                contributor_orcid = None
                contributor_attributes = None
                if f.name:
                    credit_name = CreditName(value=f.name)  # noqa: F405

                if f.email:
                    contributor_email = ContributorEmail(value=f.email)  # noqa: F405

                if f.orcid:
                    path = f.orcid

                if path:
                    url = urlparse(ORCID_BASE_URL)
                    uri = "http://" + url.hostname + "/" + path
                    host = url.hostname
                    contributor_orcid = ContributorOrcid(uri=uri, path=path, host=host)  # noqa: F405

                if f.role:
                    contributor_attributes = FundingContributorAttributes(  # noqa: F405
                        contributor_role=f.role.upper())

                funding_contributor_list.append(
                    FundingContributor(  # noqa: F405
                        contributor_orcid=contributor_orcid,
                        credit_name=credit_name,
                        contributor_email=contributor_email,
                        contributor_attributes=contributor_attributes))

            rec.contributors = FundingContributors(contributor=funding_contributor_list)  # noqa: F405
        external_id_list = []

        external_ids = ExternalIdModel.select().where(ExternalIdModel.funding_record_id == fr.id)

        for exi in external_ids:
            # Orcid is expecting external type as 'grant_number'
            external_id_type = exi.type if exi.type else "grant_number"
            external_id_value = exi.value
            external_id_url = None
            if exi.url:
                external_id_url = Url(value=exi.url)  # noqa: F405
            # Setting the external id relationship as 'SELF' by default, it can be either SELF/PART_OF
            external_id_relationship = exi.relationship.upper() if exi.relationship else "SELF"
            external_id_list.append(
                ExternalID(  # noqa: F405
                    external_id_type=external_id_type,
                    external_id_value=external_id_value,
                    external_id_url=external_id_url,
                    external_id_relationship=external_id_relationship))

        rec.external_ids = ExternalIDs(external_id=external_id_list)  # noqa: F405

        try:
            api_call = self.update_funding if put_code else self.create_funding

            params = dict(orcid=self.user.orcid, body=rec, _preload_content=False)
            if put_code:
                params["put_code"] = put_code
            resp = api_call(**params)
            app.logger.info(
                f"For {self.user} the ORCID record was {'updated' if put_code else 'created'} from {self.org}"
            )
            created = not bool(put_code)
            # retrieve the put-code from response Location header:
            if resp.status == 201:
                location = resp.headers.get("Location")
                try:
                    orcid, put_code = location.split("/")[-3::2]
                    put_code = int(put_code)
                    fi.put_code = put_code
                    fi.save()
                except:
                    app.logger.exception("Failed to get ORCID iD/put-code from the response.")
                    raise Exception("Failed to get ORCID iD/put-code from the response.")
            elif resp.status == 200:
                orcid = self.user.orcid

        except ApiException as ex:
            if ex.status == 404:
                fi.put_code = None
                fi.save()
                app.logger.exception(
                    f"For {self.user} encountered exception, So updating related put_code")
            raise ex
        except:
            app.logger.exception(f"For {self.user} encountered exception")
        else:
            return (put_code, orcid, created)

    def create_or_update_affiliation(
            self,
            affiliation=None,
            role=None,
            course_or_role=None,
            department=None,
            org_name=None,
            # NB! affiliation_record has 'organisation' field for organisation name
            organisation=None,
            city=None,
            state=None,
            region=None,
            country=None,
            disambiguated_id=None,
            disambiguation_source=None,
            start_date=None,
            end_date=None,
            put_code=None,
            initial=False,
            *args,
            **kwargs):
        """Create or update affiliation record of a user.

        :param initial: the affiliation entry created while handlind ORCID authorizastion call back.

        Returns tuple (put-code, ORCID iD, created), where created is True if a new entry
        was created, otherwise - False.
        """
        if not department:
            department = None
        if not role:
            role = None
        if not state:
            state = None
        if not region:
            region = None
        if not self.org.state:
            self.org.state = None

        if affiliation is None:
            app.logger.warning("Missing affiliation value.")
            raise Exception("Missing affiliation value.")

        if initial:
            put_code = self.is_emp_or_edu_record_present(affiliation)
            if put_code:
                return put_code, self.user.orcid, False

        organisation_address = OrganizationAddress(
            city=city or self.org.city,
            country=country or self.org.country,
            region=state or region or self.org.state)

        disambiguation_source = (lambda source: source.upper() if source else source)(
            disambiguation_source or self.org.disambiguation_source)

        disambiguated_organization_details = DisambiguatedOrganization(
            disambiguated_organization_identifier=disambiguated_id or self.org.disambiguated_id,
            disambiguation_source=disambiguation_source) if disambiguation_source and disambiguated_id else None

        if affiliation == Affiliation.EMP:
            rec = Employment()
        elif affiliation == Affiliation.EDU:
            rec = Education()
        else:
            app.logger.info(
                f"For {self.user} not able to determine affiliaton type with {self.org}")
            raise Exception(
                f"Unsupported affiliation type '{affiliation}' for {self.user} affiliaton type with {self.org}"
            )

        rec.source = self.source
        rec.organization = Organization(
            name=organisation or org_name or self.org.name,
            address=organisation_address,
            disambiguated_organization=disambiguated_organization_details)

        if put_code:
            rec.put_code = put_code

        rec.department_name = department
        rec.role_title = role or course_or_role

        if start_date:
            rec.start_date = start_date.as_orcid_dict()
        if end_date:
            rec.end_date = end_date.as_orcid_dict()

        try:
            if affiliation == Affiliation.EMP:
                api_call = self.update_employment if put_code else self.create_employment
            else:
                api_call = self.update_education if put_code else self.create_education
            params = dict(orcid=self.user.orcid, body=rec, _preload_content=False)
            if put_code:
                params["put_code"] = put_code
            resp = api_call(**params)
            app.logger.info(
                f"For {self.user} the ORCID record was {'updated' if put_code else 'created'} from {self.org}"
            )
            created = not bool(put_code)
            # retrieve the put-code from response Location header:
            if resp.status == 201:
                location = resp.headers.get("Location")
                try:
                    orcid, put_code = location.split("/")[-3::2]
                    put_code = int(put_code)
                except Exception:
                    app.logger.exception("Failed to get ORCID iD/put-code from the response.")
                    raise Exception("Failed to get ORCID iD/put-code from the response.")
            elif resp.status == 200:
                orcid = self.user.orcid

        except ApiException as apiex:
            app.logger.exception(f"For {self.user} encountered exception: {apiex}")
            raise apiex
        except Exception as ex:
            app.logger.exception(f"For {self.user} encountered exception")
            raise ex
        else:
            return (put_code, orcid, created)

    def get_webhook_access_token(self):
        """Retrieve the ORCID webhook access tokne and store it."""
        pass

    def register_webhook(self, user=None, orcid=None):
        """Register a webhook for the given ORCID ID or user."""
        pass


# yapf: disable
from orcid_api import *  # noqa: F401,F403,F405

api_client.RESTClientObject = OrcidRESTClientObject  # noqa: F405
