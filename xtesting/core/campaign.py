#!/usr/bin/env python

# Copyright (c) 2019 Orange and others.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache License, Version 2.0
# which accompanies this distribution, and is available at
# http://www.apache.org/licenses/LICENSE-2.0

"""Dump DB and artifacts for third-party certifications."""

import json
import logging
import logging.config
import mimetypes
import os
import re
import urllib
import zipfile

import boto3
from boto3.s3.transfer import TransferConfig
import botocore
import requests

from xtesting.core import testcase
from xtesting.utils import env
from xtesting.utils import config
from xtesting.utils import constants

__author__ = "Cedric Ollivier <cedric.ollivier@orange.com>"


class Campaign():
    "Dump, archive and publish all results and artifacts from a campaign."

    EX_OK = os.EX_OK
    """everything is OK"""

    EX_DUMP_FROM_DB_ERROR = os.EX_SOFTWARE - 5
    """dump_db() failed"""

    EX_DUMP_ARTIFACTS_ERROR = os.EX_SOFTWARE - 6
    """dump_artifacts() failed"""

    EX_ZIP_CAMPAIGN_FILES_ERROR = os.EX_SOFTWARE - 7
    """dump_artifacts() failed"""

    __logger = logging.getLogger(__name__)

    @staticmethod
    def dump_db():
        """Dump all test campaign results from the DB.

        It allows collecting all the results from the DB.

        It could be overriden if the common implementation is not
        suitable.

        The next vars must be set in env:

            * TEST_DB_URL,
            * BUILD_TAG.

        Returns:
            Campaign.EX_OK if results were collected from DB.
            Campaign.EX_DUMP_FROM_DB_ERROR otherwise.
        """
        try:
            url = env.get('TEST_DB_URL')
            req = requests.get(
                f"{url}?build_tag={env.get('BUILD_TAG')}",
                headers=testcase.TestCase.headers)
            req.raise_for_status()
            output = req.json()
            Campaign.__logger.debug("data from DB: \n%s", output)
            for i, _ in enumerate(output["results"]):
                for j, _ in enumerate(
                        output["results"][i]["details"]["links"]):
                    output["results"][i]["details"]["links"][j] = re.sub(
                        "^{os.environ['HTTP_DST_URL']}/*", '',
                        output["results"][i]["details"]["links"][j])
            Campaign.__logger.debug("data to archive: \n%s", output)
            with open(f"{env.get('BUILD_TAG')}.json", "w",
                      encoding='utf-8') as dfile:
                json.dump(output, dfile)
        except Exception:  # pylint: disable=broad-except
            Campaign.__logger.exception(
                "The results cannot be collected from DB")
            return Campaign.EX_DUMP_FROM_DB_ERROR
        return Campaign.EX_OK

    @staticmethod
    def dump_artifacts():
        """Dump all test campaign artifacts from the S3 repository.

        It allows collecting all the artifacts from the S3 repository.

        It could be overriden if the common implementation is not
        suitable.

        The credentials must be configured before publishing the artifacts:

            * fill ~/.aws/credentials or ~/.boto,
            * set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in env.

        The next vars must be set in env:

            * S3_ENDPOINT_URL (http://127.0.0.1:9000),
            * S3_DST_URL (s3://xtesting/prefix),

        Returns:
            Campaign.EX_OK if artifacts were published to repository.
            Campaign.EX_DUMP_ARTIFACTS_ERROR otherwise.
        """
        try:
            build_tag = env.get('BUILD_TAG')
            b3resource = boto3.resource(
                's3', endpoint_url=os.environ["S3_ENDPOINT_URL"])
            dst_s3_url = os.environ["S3_DST_URL"]
            multipart_threshold = 5 * 1024 ** 5 if "google" in os.environ[
                "S3_ENDPOINT_URL"] else 8 * 1024 * 1024
            tconfig = TransferConfig(multipart_threshold=multipart_threshold)
            bucket_name = urllib.parse.urlparse(dst_s3_url).netloc
            s3path = re.search(
                '^/*(.*)/*$', urllib.parse.urlparse(dst_s3_url).path).group(1)
            prefix = os.path.join(s3path, build_tag)
            # pylint: disable=no-member
            for s3_object in b3resource.Bucket(bucket_name).objects.filter(
                    Prefix=f"{prefix}/"):
                path, _ = os.path.split(
                    urllib.parse.unquote_plus(s3_object.key))
                lpath = re.sub(f'^{s3path}/*', '', path)
                if lpath and not os.path.exists(lpath):
                    os.makedirs(lpath)
                Campaign.__logger.info(
                    "Downloading %s",
                    re.sub(f'^{s3path}/*', '',
                           urllib.parse.unquote_plus(s3_object.key)))
                # pylint: disable=no-member
                b3resource.Bucket(bucket_name).download_file(
                    urllib.parse.unquote_plus(s3_object.key),
                    re.sub(f'^{s3path}/*', '',
                           urllib.parse.unquote_plus(s3_object.key)),
                    Config=tconfig)
            return Campaign.EX_OK
        except Exception:  # pylint: disable=broad-except
            Campaign.__logger.exception("Cannot publish the artifacts")
            return Campaign.EX_DUMP_ARTIFACTS_ERROR

    @staticmethod
    def zip_campaign_files():  # pylint: disable=too-many-locals
        """Archive and publish all test campaign data to the S3 repository.

        It allows collecting all the artifacts from the S3 repository.

        It could be overriden if the common implementation is not
        suitable.

        The credentials must be configured before publishing the artifacts:

            * fill ~/.aws/credentials or ~/.boto,
            * set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in env.

        The next vars must be set in env:

            * S3_ENDPOINT_URL (http://127.0.0.1:9000),
            * S3_DST_URL (s3://xtesting/prefix),

        Returns:
            Campaign.EX_OK if artifacts were published to repository.
            Campaign.EX_DUMP_ARTIFACTS_ERROR otherwise.
        """
        try:
            build_tag = env.get('BUILD_TAG')
            assert Campaign.dump_db() == Campaign.EX_OK
            assert Campaign.dump_artifacts() == Campaign.EX_OK
            with zipfile.ZipFile(f'{build_tag}.zip',
                                 'w', zipfile.ZIP_DEFLATED) as zfile:
                zfile.write(f"{build_tag}.json")
                for root, _, files in os.walk(build_tag):
                    for filename in files:
                        zfile.write(os.path.join(root, filename))
            b3resource = boto3.resource(
                's3', endpoint_url=os.environ["S3_ENDPOINT_URL"])
            dst_s3_url = os.environ["S3_DST_URL"]
            multipart_threshold = 5 * 1024 ** 5 if "google" in os.environ[
                "S3_ENDPOINT_URL"] else 8 * 1024 * 1024
            tconfig = TransferConfig(multipart_threshold=multipart_threshold)
            bucket_name = urllib.parse.urlparse(dst_s3_url).netloc
            mime_type = mimetypes.guess_type(f'{build_tag}.zip')
            path = urllib.parse.urlparse(dst_s3_url).path.strip("/")
            # pylint: disable=no-member
            b3resource.Bucket(bucket_name).upload_file(
                f'{build_tag}.zip',
                os.path.join(path, f'{build_tag}.zip'),
                Config=tconfig,
                ExtraArgs={'ContentType': mime_type[
                    0] or 'application/octet-stream'})
            dst_http_url = os.environ["HTTP_DST_URL"]
            link = os.path.join(dst_http_url, f'{build_tag}.zip')
            Campaign.__logger.info(
                "All data were successfully published:\n\n%s", link)
            return Campaign.EX_OK
        except KeyError as ex:
            Campaign.__logger.error("Please check env var: %s", str(ex))
            return Campaign.EX_ZIP_CAMPAIGN_FILES_ERROR
        except botocore.exceptions.NoCredentialsError:
            Campaign.__logger.error(
                "Please fill ~/.aws/credentials, ~/.boto or set "
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in env")
            return Campaign.EX_ZIP_CAMPAIGN_FILES_ERROR
        except Exception:  # pylint: disable=broad-except
            Campaign.__logger.exception("Cannot publish the artifacts")
            return Campaign.EX_ZIP_CAMPAIGN_FILES_ERROR


def main():
    """Entry point for Campaign.zip_campaign_files()."""
    if not os.path.exists(testcase.TestCase.dir_results):
        os.makedirs(testcase.TestCase.dir_results)
    if env.get('DEBUG').lower() == 'true':
        logging.config.fileConfig(config.get_xtesting_config(
            'logging.debug.ini', constants.DEBUG_INI_PATH_DEFAULT))
    else:
        logging.config.fileConfig(config.get_xtesting_config(
            'logging.ini', constants.INI_PATH_DEFAULT))
    logging.captureWarnings(True)
    os.chdir(testcase.TestCase.dir_results)
    Campaign.zip_campaign_files()
