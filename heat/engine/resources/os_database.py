# vim: tabstop=4 shiftwidth=4 softtabstop=4

#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

try:
    from troveclient.openstack.common.apiclient.exceptions import NotFound
except ImportError:
    #Setup fake exception for unit testing without troveclient
    class NotFound(Exception):
        pass

from heat.common import exception
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource
from heat.engine.resources import nova_utils
from heat.openstack.common import log as logging


logger = logging.getLogger(__name__)


class OSDBInstance(resource.Resource):
    '''
    Openstack cloud database instance resource.
    '''
    database_schema = {
        "character_set": properties.Schema(
            properties.Schema.STRING,
            _("Set of symbols and encodings."),
            default="utf8",
            required=False),

        "collate": properties.Schema(
            properties.Schema.STRING,
            _("Set of rules for comparing characters in a character set."),
            default="utf8_general_ci",
            required=False),

        "name": properties.Schema(
            properties.Schema.STRING,
            _("Specifies database names for creating databases on instance"
              " creation."),
            required=True,
            constraints=[
                constraints.Length(max=64),
                constraints.AllowedPattern(
                    "[a-zA-Z0-9_]+[a-zA-Z0-9_@?#\s]*[a-zA-Z0-9_]+")]),
    }

    user_schema = {
        "name": properties.Schema(
            properties.Schema.STRING,
            _("User name to create a user on instance creation."),
            required=True,
            constraints=[
                constraints.Length(max=16),
                constraints.AllowedPattern(
                    "[a-zA-Z0-9_]+[a-zA-Z0-9_@?#\s]*[a-zA-Z0-9_]+")]),

        "password": properties.Schema(
            properties.Schema.STRING,
            _("Password for those users on instance creation."),
            required=True,
            constraints=[constraints.AllowedPattern(
                "[a-zA-Z0-9_]+[a-zA-Z0-9_@?#\s]*[a-zA-Z0-9_]+")]),

        "host": properties.Schema(
            properties.Schema.STRING,
            _("The host from which a user is allowed to connect "
              "to the database."),
            default="%"),

        "databases": properties.Schema(
            properties.Schema.LIST,
            _("Names of databases that those users can access "
              "on instance creation."),
            required=True,
            schema=properties.Schema(properties.Schema.STRING))
    }

    properties_schema = {
        "name": properties.Schema(
            properties.Schema.STRING,
            _("Name of the DB instance to create."),
            required=True,
            constraints=[constraints.Length(max=255)]),

        "flavor": properties.Schema(
            properties.Schema.STRING,
            _("Reference to a flavor for creating DB instance."),
            required=True),

        "size": properties.Schema(
            properties.Schema.INTEGER,
            _("Database volume size in GB."),
            required=True,
            constraints=[constraints.Range(1, 150)]),

        "databases": properties.Schema(
            properties.Schema.LIST,
            _("List of databases to be created on DB instance creation."),
            required=False,
            default=[],
            schema=properties.Schema(properties.Schema.MAP,
                                     schema=database_schema)),

        "users": properties.Schema(
            properties.Schema.LIST,
            _("List of users to be created on DB instance creation."),
            required=False,
            default=[],
            schema=properties.Schema(properties.Schema.MAP,
                                     schema=user_schema)),

        "availability_zone": properties.Schema(
            properties.Schema.STRING,
            _("Name of the availability zone for DB instance.")),

        "restore_point": properties.Schema(
            properties.Schema.STRING,
            _("DB instance restore point."))

    }

    attributes_schema = {
        "hostname": _("Hostname of the instance"),
        "href": _("Api endpoint reference of the instance")
    }

    def __init__(self, name, json_snippet, stack):
        super(OSDBInstance, self).__init__(name, json_snippet, stack)
        self._href = None
        self._dbinstance = None

    @property
    def dbinstance(self):
        """Get the trove dbinstance."""
        if not self._dbinstance and self.resource_id:
            self._dbinstance = self.trove().instances.get(self.resource_id)

        return self._dbinstance

    def physical_resource_name(self):
        name = self.properties.get('name')
        if name:
            return name

        return super(OSDBInstance, self).physical_resource_name()

    def handle_create(self):
        '''
        Create cloud database instance.
        '''
        self.dbinstancename = self.physical_resource_name()
        self.flavor = nova_utils.get_flavor_id(self.trove(),
                                               self.properties['flavor'])
        self.volume = {'size': self.properties['size']}
        self.databases = self.properties.get('databases', [])
        self.users = self.properties.get('users', [])
        restore_point = self.properties.get('restore_point', None)
        zone = self.properties.get('availability_zone', None)

        # convert user databases to format required for troveclient.
        # that is, list of database dictionaries
        for user in self.users:
            user['databases'] = [{'name': db}
                                 for db in user.get('databases', [])]

        # create db instance
        instance = self.trove().instances.create(
            self.dbinstancename,
            self.flavor,
            volume=self.volume,
            databases=self.databases,
            users=self.users,
            restorePoint=restore_point,
            availability_zone=zone)
        self.resource_id_set(instance.id)

        return instance

    def check_create_complete(self, instance):
        '''
        Check if cloud DB instance creation is complete.
        '''
        instance.get()  # get updated attributes
        if instance.status == 'ERROR':
            raise exception.Error(_("Database instance creation failed."))

        if instance.status != 'ACTIVE':
            return False

        msg = _("Database instance %(database)s created (flavor:%(flavor)s, "
                "volume:%(volume)s)")
        logger.info(msg % ({'database': self.dbinstancename,
                            'flavor': self.flavor,
                            'volume': self.volume}))
        return True

    def handle_delete(self):
        '''
        Delete a cloud database instance.
        '''
        if not self.resource_id:
            return

        instance = None
        try:
            instance = self.trove().instances.get(self.resource_id)
        except NotFound:
            logger.debug(_("Database instance %s not found.") %
                         self.resource_id)
            self.resource_id_set(None)
        else:
            instance.delete()
            return instance

    def check_delete_complete(self, instance):
        '''
        Check for completion of cloud DB instance delettion
        '''
        if not instance:
            return True

        try:
            instance.get()
        except NotFound:
            self.resource_id_set(None)
            return True

        return False

    def validate(self):
        '''
        Validate any of the provided params
        '''
        res = super(OSDBInstance, self).validate()
        if res:
            return res

        # check validity of user and databases
        users = self.properties.get('users', [])
        if not users:
            return

        databases = self.properties.get('databases', [])
        if not databases:
            msg = _('Databases property is required if users property'
                    ' is provided')
            raise exception.StackValidationFailed(message=msg)

        for user in users:
            if not user.get('databases', []):
                msg = _('Must provide access to at least one database for '
                        'user %s') % user['name']
                raise exception.StackValidationFailed(message=msg)

            missing_db = [db_name for db_name in user['databases']
                          if db_name not in
                          [db['name'] for db in databases]]

            if missing_db:
                msg = _('Database %s specified for user does not exist in '
                        'databases.') % missing_db
                raise exception.StackValidationFailed(message=msg)

    def href(self):
        if not self._href and self.dbinstance:
            if not self.dbinstance.links:
                self._href = None
            else:
                for link in self.dbinstance.links:
                    if link['rel'] == 'self':
                        self._href = link['href']
                        break

        return self._href

    def _resolve_attribute(self, name):
        if name == 'hostname':
            return self.dbinstance.hostname
        elif name == 'href':
            return self.href()


def resource_mapping():
    return {
        'OS::Trove::Instance': OSDBInstance,
    }