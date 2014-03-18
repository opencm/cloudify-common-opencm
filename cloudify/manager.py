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

__author__ = 'idanmo'

import urllib2
import os
import tempfile
import utils
from cosmo_manager_rest_client.cosmo_manager_rest_client \
    import CosmoManagerRestClient


class NodeState(object):
    """Represents a deployment node state.
    An instance of this class contains runtime information retrieved
    from Cloudify's runtime storage.
    Its API allows to set and get properties of the node's state,
     generate an updates dict to be used when requesting to save changes
     back to the storage (in an optimistic locking manner).
    """
    def __init__(self, node_id, runtime_properties=None, state_version=None):
        self.id = node_id
        self._runtime_properties = (runtime_properties or {}).copy()
        self._state_version = state_version

    def get(self, key):
        return self._runtime_properties.get(key)

    def put(self, key, value):
        self._runtime_properties[key] = value

    __setitem__ = put

    __getitem__ = get

    def __contains__(self, key):
        return key in self._runtime_properties

    @property
    def runtime_properties(self):
        return self._runtime_properties.copy()

    @property
    def state_version(self):
        return self._state_version


def get_manager_rest_client():
    return CosmoManagerRestClient(utils.get_manager_ip(),
                                  utils.get_manager_rest_service_port())


def get_resource(resource_path, blueprint_id, logger, target_path=None):
    url = '{0}/{1}/{2}'.format(
        utils.get_manager_file_server_blueprints_root_url(),
        blueprint_id,
        resource_path)
    return _download(url, logger, target_path)


def _download(url, logger, target_path=None):
    """downloads a file to the local disk and returns it's disk path"""
    try:
        resp = urllib2.urlopen(url)

        if not target_path:
            (fd, target_path) = tempfile.mkstemp()
            with os.fdopen(fd, 'w') as f:
                f.write(resp.read())
        else:
            with open(target_path, 'w') as f:
                f.write(resp.read())

        logger.info("Downloaded %s to %s" % (url, target_path))
        return target_path
    except (IOError, urllib2.HTTPError) as e:
        logger.error("Error downloading file %s. %s" % (url, e))
        return None


def get_node_state(node_id):
    client = get_manager_rest_client()
    node_state = client.get_node_state(node_id)
    if 'runtimeInfo' not in node_state:
        raise KeyError('runtimeInfo not found in get_node_state response')
    if 'stateVersion' not in node_state:
        raise KeyError('stateVersion not found in get_node_state response')
    return NodeState(
        node_id, node_state['runtimeInfo'], node_state['stateVersion'])


def update_node_state(node_state):
    client = get_manager_rest_client()
    client.update_node_state(node_state.id, node_state.runtime_properties,
                             node_state.state_version)
