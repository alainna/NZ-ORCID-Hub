# coding: utf-8

"""
    ORCID Member

    No description provided (generated by Swagger Codegen https://github.com/swagger-api/swagger-codegen)

    OpenAPI spec version: Latest
    
    Generated by: https://github.com/swagger-api/swagger-codegen.git
"""


from pprint import pformat
from six import iteritems
import re


class Educations(object):
    """
    NOTE: This class is auto generated by the swagger code generator program.
    Do not edit the class manually.
    """
    def __init__(self, last_modified_date=None, education_summary=None, path=None):
        """
        Educations - a model defined in Swagger

        :param dict swaggerTypes: The key is attribute name
                                  and the value is attribute type.
        :param dict attributeMap: The key is attribute name
                                  and the value is json key in definition.
        """
        self.swagger_types = {
            'last_modified_date': 'LastModifiedDate',
            'education_summary': 'list[EducationSummary]',
            'path': 'str'
        }

        self.attribute_map = {
            'last_modified_date': 'last-modified-date',
            'education_summary': 'education-summary',
            'path': 'path'
        }

        self._last_modified_date = last_modified_date
        self._education_summary = education_summary
        self._path = path

    @property
    def last_modified_date(self):
        """
        Gets the last_modified_date of this Educations.

        :return: The last_modified_date of this Educations.
        :rtype: LastModifiedDate
        """
        return self._last_modified_date

    @last_modified_date.setter
    def last_modified_date(self, last_modified_date):
        """
        Sets the last_modified_date of this Educations.

        :param last_modified_date: The last_modified_date of this Educations.
        :type: LastModifiedDate
        """

        self._last_modified_date = last_modified_date

    @property
    def education_summary(self):
        """
        Gets the education_summary of this Educations.

        :return: The education_summary of this Educations.
        :rtype: list[EducationSummary]
        """
        return self._education_summary

    @education_summary.setter
    def education_summary(self, education_summary):
        """
        Sets the education_summary of this Educations.

        :param education_summary: The education_summary of this Educations.
        :type: list[EducationSummary]
        """

        self._education_summary = education_summary

    @property
    def path(self):
        """
        Gets the path of this Educations.

        :return: The path of this Educations.
        :rtype: str
        """
        return self._path

    @path.setter
    def path(self, path):
        """
        Sets the path of this Educations.

        :param path: The path of this Educations.
        :type: str
        """

        self._path = path

    def to_dict(self):
        """
        Returns the model properties as a dict
        """
        result = {}

        for attr, _ in iteritems(self.swagger_types):
            value = getattr(self, attr)
            if isinstance(value, list):
                result[attr] = list(map(
                    lambda x: x.to_dict() if hasattr(x, "to_dict") else x,
                    value
                ))
            elif hasattr(value, "to_dict"):
                result[attr] = value.to_dict()
            elif isinstance(value, dict):
                result[attr] = dict(map(
                    lambda item: (item[0], item[1].to_dict())
                    if hasattr(item[1], "to_dict") else item,
                    value.items()
                ))
            else:
                result[attr] = value

        return result

    def to_str(self):
        """
        Returns the string representation of the model
        """
        return pformat(self.to_dict())

    def __repr__(self):
        """
        For `print` and `pprint`
        """
        return self.to_str()

    def __eq__(self, other):
        """
        Returns true if both objects are equal
        """
        if not isinstance(other, Educations):
            return False

        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        """
        Returns true if both objects are not equal
        """
        return not self == other
