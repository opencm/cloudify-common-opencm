########
# Copyright (c) 2014 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.

import json
import logging

import requests
from base64 import urlsafe_b64encode
from requests.packages import urllib3

from cloudify_rest_client import exceptions
from cloudify_rest_client.blueprints import BlueprintsClient
from cloudify_rest_client.snapshots import SnapshotsClient
from cloudify_rest_client.deployments import DeploymentsClient
from cloudify_rest_client.deployment_updates import DeploymentUpdatesClient
from cloudify_rest_client.executions import ExecutionsClient
from cloudify_rest_client.nodes import NodesClient
from cloudify_rest_client.node_instances import NodeInstancesClient
from cloudify_rest_client.events import EventsClient
from cloudify_rest_client.manager import ManagerClient
from cloudify_rest_client.search import SearchClient
from cloudify_rest_client.evaluate import EvaluateClient
from cloudify_rest_client.deployment_modifications import (
    DeploymentModificationsClient)
from cloudify_rest_client.tokens import TokensClient
from cloudify_rest_client.plugins import PluginsClient
from cloudify_rest_client.maintenance import MaintenanceModeClient
from cloudify_rest_client.tenants import TenantsClient
from cloudify_rest_client.user_groups import UserGroupsClient
from cloudify_rest_client.users import UsersClient
from cloudify_rest_client.cluster import ClusterClient
from cloudify_rest_client.ldap import LdapClient
from cloudify_rest_client.secrets import SecretsClient


DEFAULT_PORT = 80
SECURED_PORT = 443
SECURED_PROTOCOL = 'https'
DEFAULT_PROTOCOL = 'http'
DEFAULT_API_VERSION = 'v3.1'
BASIC_AUTH_PREFIX = 'Basic'
CLOUDIFY_TENANT_HEADER = 'Tenant'
CLOUDIFY_AUTHENTICATION_HEADER = 'Authorization'
CLOUDIFY_TOKEN_AUTHENTICATION_HEADER = 'Authentication-Token'

urllib3.disable_warnings(urllib3.exceptions.InsecurePlatformWarning)


class HTTPClient(object):
    default_timeout_sec = None

    def __init__(self, host, port=DEFAULT_PORT,
                 protocol=DEFAULT_PROTOCOL, api_version=DEFAULT_API_VERSION,
                 headers=None, query_params=None, cert=None, trust_all=False,
                 username=None, password=None, token=None, tenant=None):
        self.port = port
        self.host = host
        self.protocol = protocol
        self.api_version = api_version

        self.headers = headers.copy() if headers else {}
        if not self.headers.get('Content-type'):
            self.headers['Content-type'] = 'application/json'
        self.query_params = query_params.copy() if query_params else {}
        self.logger = logging.getLogger('cloudify.rest_client.http')
        self.cert = cert
        self.trust_all = trust_all
        self._set_header(CLOUDIFY_AUTHENTICATION_HEADER,
                         self._get_auth_header(username, password),
                         log_value=False)
        self._set_header(CLOUDIFY_TOKEN_AUTHENTICATION_HEADER, token)
        self._set_header(CLOUDIFY_TENANT_HEADER, tenant)

    @property
    def url(self):
        return '{0}://{1}:{2}/api/{3}'.format(self.protocol, self.host,
                                              self.port, self.api_version)

    def _raise_client_error(self, response, url=None):
        try:
            result = response.json()
        except Exception:
            if response.status_code == 304:
                error_msg = 'Nothing to modify'
                self._prepare_and_raise_exception(
                    message=error_msg,
                    error_code='not_modified',
                    status_code=response.status_code,
                    server_traceback='')
            else:
                message = response.content
                if url:
                    message = '{0} [{1}]'.format(message, url)
                error_msg = '{0}: {1}'.format(response.status_code, message)
            raise exceptions.CloudifyClientError(
                error_msg,
                status_code=response.status_code)
        message = result['message']
        code = result.get('error_code')
        server_traceback = result.get('server_traceback')
        self._prepare_and_raise_exception(
            message=message,
            error_code=code,
            status_code=response.status_code,
            server_traceback=server_traceback,
            response=response)

    @staticmethod
    def _prepare_and_raise_exception(message,
                                     error_code,
                                     status_code,
                                     server_traceback=None,
                                     response=None):

        error = exceptions.ERROR_MAPPING.get(error_code,
                                             exceptions.CloudifyClientError)
        raise error(message, server_traceback,
                    status_code, error_code=error_code, response=response)

    def verify_response_status(self, response, expected_code=200):
        if response.status_code != expected_code:
            self._raise_client_error(response)

    def _do_request(self, requests_method, request_url, body, params, headers,
                    expected_status_code, stream, verify, timeout):
        response = requests_method(request_url,
                                   data=body,
                                   params=params,
                                   headers=headers,
                                   stream=stream,
                                   verify=verify,
                                   timeout=timeout or self.default_timeout_sec)
        if self.logger.isEnabledFor(logging.DEBUG):
            for hdr, hdr_content in response.request.headers.iteritems():
                self.logger.debug('request header:  %s: %s'
                                  % (hdr, hdr_content))
            self.logger.debug('reply:  "%s %s" %s'
                              % (response.status_code,
                                 response.reason, response.content))
            for hdr, hdr_content in response.headers.iteritems():
                self.logger.debug('response header:  %s: %s'
                                  % (hdr, hdr_content))

        if response.status_code != expected_status_code:
            self._raise_client_error(response, request_url)

        if stream:
            return StreamedResponse(response)

        response_json = response.json()

        if response.history:
            response_json['history'] = response.history

        return response_json

    def get_request_verify(self):
        # disable certificate verification if user asked us to.
        if self.trust_all:
            return False
        # verify will hold the path to the self-signed certificate
        if self.cert:
            return self.cert
        # verify the certificate
        return True

    def do_request(self,
                   requests_method,
                   uri,
                   data=None,
                   params=None,
                   headers=None,
                   expected_status_code=200,
                   stream=False,
                   versioned_url=True,
                   timeout=None):
        if versioned_url:
            request_url = '{0}{1}'.format(self.url, uri)
        else:
            # remove version from url ending
            url = self.url.rsplit('/', 1)[0]
            request_url = '{0}{1}'.format(url, uri)

        # build headers
        headers = headers or {}
        total_headers = self.headers.copy()
        total_headers.update(headers)

        # build query params
        params = params or {}
        total_params = self.query_params.copy()
        total_params.update(params)

        # data is either dict, bytes data or None
        is_dict_data = isinstance(data, dict)
        body = json.dumps(data) if is_dict_data else data
        if self.logger.isEnabledFor(logging.DEBUG):
            log_message = 'Sending request: {0} {1}'.format(
                requests_method.func_name.upper(),
                request_url)
            if is_dict_data:
                log_message += '; body: {0}'.format(body)
            elif data is not None:
                log_message += '; body: bytes data'
            self.logger.debug(log_message)
        try:
            return self._do_request(
                requests_method=requests_method, request_url=request_url,
                body=body, params=total_params, headers=total_headers,
                expected_status_code=expected_status_code, stream=stream,
                verify=self.get_request_verify(), timeout=timeout)
        except requests.exceptions.SSLError as e:
            raise requests.exceptions.SSLError(
                'An SSL-related error has occurred. This can happen if the '
                'specified REST certificate does not match the certificate on '
                'the manager. Underlying reason: {0}'.format(e))
        except requests.exceptions.ConnectionError as e:
            raise requests.exceptions.ConnectionError(
                '{0}\nThis can happen when the manager is not working with '
                'SSL, but the client does'.format(e)
            )

    def get(self, uri, data=None, params=None, headers=None, _include=None,
            expected_status_code=200, stream=False, versioned_url=True,
            timeout=None):
        if _include:
            fields = ','.join(_include)
            if not params:
                params = {}
            params['_include'] = fields
        return self.do_request(requests.get,
                               uri,
                               data=data,
                               params=params,
                               headers=headers,
                               expected_status_code=expected_status_code,
                               stream=stream,
                               versioned_url=versioned_url,
                               timeout=timeout)

    def put(self, uri, data=None, params=None, headers=None,
            expected_status_code=200, stream=False, timeout=None):
        return self.do_request(requests.put,
                               uri,
                               data=data,
                               params=params,
                               headers=headers,
                               expected_status_code=expected_status_code,
                               stream=stream,
                               timeout=timeout)

    def patch(self, uri, data=None, params=None, headers=None,
              expected_status_code=200, stream=False, timeout=None):
        return self.do_request(requests.patch,
                               uri,
                               data=data,
                               params=params,
                               headers=headers,
                               expected_status_code=expected_status_code,
                               stream=stream,
                               timeout=timeout)

    def post(self, uri, data=None, params=None, headers=None,
             expected_status_code=200, stream=False, timeout=None):
        return self.do_request(requests.post,
                               uri,
                               data=data,
                               params=params,
                               headers=headers,
                               expected_status_code=expected_status_code,
                               stream=stream,
                               timeout=timeout)

    def delete(self, uri, data=None, params=None, headers=None,
               expected_status_code=200, stream=False, timeout=None):
        return self.do_request(requests.delete,
                               uri,
                               data=data,
                               params=params,
                               headers=headers,
                               expected_status_code=expected_status_code,
                               stream=stream,
                               timeout=timeout)

    def _get_auth_header(self, username, password):
        if not username or not password:
            return None
        credentials = '{0}:{1}'.format(username, password)
        encoded_credentials = urlsafe_b64encode(credentials)
        return BASIC_AUTH_PREFIX + ' ' + encoded_credentials

    def _set_header(self, key, value, log_value=True):
        if not value:
            return
        self.headers[key] = value
        value = value if log_value else '*'
        self.logger.debug('Setting `{0}` header: {1}'.format(key, value))


class StreamedResponse(object):

    def __init__(self, response):
        self._response = response

    @property
    def headers(self):
        return self._response.headers

    def bytes_stream(self, chunk_size=8192):
        return self._response.iter_content(chunk_size)

    def lines_stream(self):
        return self._response.iter_lines()

    def close(self):
        self._response.close()


class CloudifyClient(object):
    """Cloudify's management client."""
    client_class = HTTPClient

    def __init__(self, host='localhost', port=None, protocol=DEFAULT_PROTOCOL,
                 api_version=DEFAULT_API_VERSION, headers=None,
                 query_params=None, cert=None, trust_all=False,
                 username=None, password=None, token=None, tenant=None):
        """
        Creates a Cloudify client with the provided host and optional port.

        :param host: Host of Cloudify's management machine.
        :param port: Port of REST API service on management machine.
        :param protocol: Protocol of REST API service on management machine,
                        defaults to http.
        :param api_version: version of REST API service on management machine.
        :param headers: Headers to be added to request.
        :param query_params: Query parameters to be added to the request.
        :param cert: Path to a copy of the server's self-signed certificate.
        :param trust_all: if `False`, the server's certificate
                          (self-signed or not) will be verified.
        :param username: Cloudify User username.
        :param password: Cloudify User password.
        :param token: Cloudify User token.
        :param tenant: Cloudify Tenant name.
        :return: Cloudify client instance.
        """

        if not port:
            if protocol == SECURED_PROTOCOL:
                # SSL
                port = SECURED_PORT
            else:
                port = DEFAULT_PORT

        self.host = host
        self._client = self.client_class(host, port, protocol, api_version,
                                         headers, query_params, cert,
                                         trust_all, username, password,
                                         token, tenant)
        self.blueprints = BlueprintsClient(self._client)
        self.snapshots = SnapshotsClient(self._client)
        self.deployments = DeploymentsClient(self._client)
        self.executions = ExecutionsClient(self._client)
        self.nodes = NodesClient(self._client)
        self.node_instances = NodeInstancesClient(self._client)
        self.manager = ManagerClient(self._client)
        self.events = EventsClient(self._client)
        self.search = SearchClient(self._client)
        self.evaluate = EvaluateClient(self._client)
        self.deployment_modifications = DeploymentModificationsClient(
            self._client)
        self.tokens = TokensClient(self._client)
        self.plugins = PluginsClient(self._client)
        self.maintenance_mode = MaintenanceModeClient(self._client)
        self.deployment_updates = DeploymentUpdatesClient(self._client)
        self.tenants = TenantsClient(self._client)
        self.user_groups = UserGroupsClient(self._client)
        self.users = UsersClient(self._client)
        self.cluster = ClusterClient(self._client)
        self.ldap = LdapClient(self._client)
        self.secrets = SecretsClient(self._client)
