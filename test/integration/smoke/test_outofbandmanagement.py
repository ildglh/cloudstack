# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.


import marvin
from marvin.cloudstackTestCase import *
from marvin.cloudstackAPI import *
from marvin.lib.utils import *
from marvin.lib.base import *
from marvin.lib.common import *
from marvin.lib.utils import (random_gen)
from nose.plugins.attrib import attr

from ipmisim.ipmisim import IpmiServerContext, IpmiServer, ThreadedIpmiServer

import random
import socket
import sys
import thread
import time


class TestOutOfBandManagement(cloudstackTestCase):
    """ Test cases for out of band management
    """

    def setUp(self):
        self.apiclient = self.testClient.getApiClient()
        self.hypervisor = self.testClient.getHypervisorInfo()
        self.dbclient = self.testClient.getDbConnection()
        self.services = self.testClient.getParsedTestDataConfig()
        self.mgtSvrDetails = self.config.__dict__["mgtSvr"][0].__dict__
        self.fakeMsId = random.randint(10000, 99999) * random.randint(10, 20)

        self.zone = get_zone(self.apiclient, self.testClient.getZoneForTests())
        self.host = None
        self.server = None

        # use random port for ipmisim
        s = socket.socket()
        s.bind(('', 0))
        self.serverPort = s.getsockname()[1]
        s.close()

        self.cleanup = []


    def tearDown(self):
        try:
            self.dbclient.execute("delete from oobm where port=%d" % self.getIpmiServerPort())
            self.dbclient.execute("delete from mshost_peer where peer_runid=%s" % self.getFakeMsRunId())
            self.dbclient.execute("delete from mshost where runid=%s" % self.getFakeMsRunId())
            self.dbclient.execute("delete from cluster_details where name='outOfBandManagementEnabled'")
            self.dbclient.execute("delete from data_center_details where name='outOfBandManagementEnabled'")
            cleanup_resources(self.apiclient, self.cleanup)
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                IpmiServerContext('reset')
        except Exception as e:
            raise Exception("Warning: Exception during cleanup : %s" % e)


    def getFakeMsId(self):
        return self.fakeMsId


    def getFakeMsRunId(self):
        return self.fakeMsId * 1000


    def getHost(self, hostId=None):
        if self.host and hostId is None:
            return self.host

        response = list_hosts(
                        self.apiclient,
                        zoneid=self.zone.id,
                        type='Routing',
                        id=hostId
                        )
        if len(response) > 0:
            self.host = response[0]
            return self.host
        raise self.skipTest("No hosts found, skipping out-of-band management test")


    def getIpmiServerIp(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((self.mgtSvrDetails["mgtSvrIp"], self.mgtSvrDetails["port"]))
        return s.getsockname()[0]


    def getIpmiServerPort(self):
        return self.serverPort


    def getOobmConfigCmd(self):
        cmd = configureOutOfBandManagement.configureOutOfBandManagementCmd()
        cmd.driver = 'ipmitool' # The default available driver
        cmd.address = self.getIpmiServerIp()
        cmd.port = self.getIpmiServerPort()
        cmd.username = 'admin'
        cmd.password = 'password'
        cmd.hostid = self.getHost().id
        return cmd


    def getOobmEnableCmd(self):
        cmd = enableOutOfBandManagementForHost.enableOutOfBandManagementForHostCmd()
        cmd.hostid = self.getHost().id
        return cmd


    def getOobmDisableCmd(self):
        cmd = disableOutOfBandManagementForHost.disableOutOfBandManagementForHostCmd()
        cmd.hostid = self.getHost().id
        return cmd


    def getOobmIssueActionCmd(self):
        cmd = issueOutOfBandManagementPowerAction.issueOutOfBandManagementPowerActionCmd()
        cmd.hostid = self.getHost().id
        cmd.action = 'STATUS'
        return cmd


    def issuePowerActionCmd(self, action, timeout=None):
        cmd = self.getOobmIssueActionCmd()
        cmd.action = action
        if timeout:
            cmd.timeout = timeout

        try:
            return self.apiclient.issueOutOfBandManagementPowerAction(cmd)
        except Exception as e:
            if "packet session id 0x0 does not match active session" in str(e):
                raise self.skipTest("Known ipmitool issue hit, skipping test")
            raise e


    def configureAndEnableOobm(self):
        self.apiclient.configureOutOfBandManagement(self.getOobmConfigCmd())
        response = self.apiclient.enableOutOfBandManagementForHost(self.getOobmEnableCmd())
        self.assertEqual(response.enabled, True)


    def startIpmiServer(self):
        def startIpmiServer(tname, server):
            self.debug("Starting ipmisim server")
            try:
                server.serve_forever()
            except Exception: pass
        IpmiServerContext('reset')
        ThreadedIpmiServer.allow_reuse_address = False
        server = ThreadedIpmiServer(('0.0.0.0', self.getIpmiServerPort()), IpmiServer)
        thread.start_new_thread(startIpmiServer, ("ipmi-server", server,))
        self.server = server


    def checkSyncToState(self, state, interval):
        def checkForStateSync(expectedState):
            response = self.getHost(hostId=self.getHost().id).outofbandmanagement
            return response.powerstate == expectedState, None

        sync_interval = 1 + int(interval)/1000
        res, _ = wait_until(sync_interval, 10, checkForStateSync, state)
        if not res:
            self.fail("Failed to get host.powerstate synced to expected state:" + state)
        response = self.getHost(hostId=self.getHost().id).outofbandmanagement
        self.assertEqual(response.powerstate, state)


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_configure_invalid_driver(self):
        """
            Tests out-of-band management configuration with invalid driver
        """
        cmd = self.getOobmConfigCmd()
        cmd.driver = 'randomDriverThatDoesNotExist'
        try:
            response = self.apiclient.configureOutOfBandManagement(cmd)
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_configure_default_driver(self):
        """
            Tests out-of-band management configuration with valid data
        """
        cmd = self.getOobmConfigCmd()
        response = self.apiclient.configureOutOfBandManagement(cmd)
        self.assertEqual(response.hostid, cmd.hostid)
        self.assertEqual(response.driver, cmd.driver)
        self.assertEqual(response.address, cmd.address)
        self.assertEqual(response.port, str(cmd.port))
        self.assertEqual(response.username, cmd.username)


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_enable_feature_invalid(self):
        """
            Tests out-of-band management host enable feature with
            invalid options
        """
        cmd = self.getOobmEnableCmd()
        cmd.hostid = -1
        try:
            response = self.apiclient.enableOutOfBandManagementForHost(cmd)
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass

        try:
            cmd = enableOutOfBandManagementForCluster.enableOutOfBandManagementForClusterCmd()
            response = self.apiclient.enableOutOfBandManagementForCluster(cmd)
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass

        try:
            cmd = enableOutOfBandManagementForZone.enableOutOfBandManagementForZoneCmd()
            response = self.apiclient.enableOutOfBandManagementForZone(cmd)
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_disable_feature_invalid(self):
        """
            Tests out-of-band management host disable feature with
            invalid options
        """
        cmd = self.getOobmDisableCmd()
        cmd.hostid = -1
        try:
            response = self.apiclient.disableOutOfBandManagementForHost(cmd)
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass

        try:
            cmd = disableOutOfBandManagementForCluster.disableOutOfBandManagementForClusterCmd()
            response = self.apiclient.disableOutOfBandManagementForCluster(cmd)
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass

        try:
            cmd = disableOutOfBandManagementForZone.disableOutOfBandManagementForZoneCmd()
            response = self.apiclient.disableOutOfBandManagementForZone(cmd)
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_enable_feature_valid(self):
        """
            Tests out-of-band management host enable feature with
            valid options
        """
        self.apiclient.configureOutOfBandManagement(self.getOobmConfigCmd())
        cmd = self.getOobmEnableCmd()
        response = self.apiclient.enableOutOfBandManagementForHost(cmd)
        self.assertEqual(response.hostid, cmd.hostid)
        self.assertEqual(response.enabled, True)


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_disable_feature_valid(self):
        """
            Tests out-of-band management host disable feature with
            valid options
        """

        self.apiclient.configureOutOfBandManagement(self.getOobmConfigCmd())
        cmd = self.getOobmDisableCmd()
        response = self.apiclient.disableOutOfBandManagementForHost(cmd)
        self.assertEqual(response.hostid, cmd.hostid)
        self.assertEqual(response.enabled, False)

        response = self.getHost(hostId=cmd.hostid).outofbandmanagement
        self.assertEqual(response.powerstate, 'Disabled')


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_enabledisable_across_clusterzones(self):
        """
            Tests out-of-band management enable/disable feature at cluster
            and zone level sequentially Zone > Cluster > Host
        """
        self.configureAndEnableOobm()
        self.startIpmiServer()
        bmc = IpmiServerContext().bmc
        bmc.powerstate = 'off'

        host = self.getHost()

        # Disable at zone level
        cmd = disableOutOfBandManagementForZone.disableOutOfBandManagementForZoneCmd()
        cmd.zoneid = host.zoneid
        response = self.apiclient.disableOutOfBandManagementForZone(cmd)

        # Disable at cluster level
        cmd = disableOutOfBandManagementForCluster.disableOutOfBandManagementForClusterCmd()
        cmd.clusterid = host.clusterid
        response = self.apiclient.disableOutOfBandManagementForCluster(cmd)

        # Disable at host level
        cmd = disableOutOfBandManagementForHost.disableOutOfBandManagementForHostCmd()
        cmd.hostid = host.id
        response = self.apiclient.disableOutOfBandManagementForHost(cmd)

        try:
            self.issuePowerActionCmd('STATUS')
            self.fail("Exception was expected, oobm is disabled at zone level")
        except Exception: pass

        # Enable at zone level
        cmd = enableOutOfBandManagementForZone.enableOutOfBandManagementForZoneCmd()
        cmd.zoneid = host.zoneid
        response = self.apiclient.enableOutOfBandManagementForZone(cmd)

        try:
            self.issuePowerActionCmd('STATUS')
            self.fail("Exception was expected, oobm is disabled at cluster level")
        except Exception: pass

        # Check background thread syncs state to Disabled
        response = self.getHost(hostId=host.id).outofbandmanagement
        self.assertEqual(response.powerstate, 'Disabled')
        self.dbclient.execute("update oobm set power_state='On' where port=%d" % self.getIpmiServerPort())
        interval = list_configurations(
            self.apiclient,
            name='outofbandmanagement.sync.interval'
        )[0].value
        self.checkSyncToState('Disabled', interval)

        # Enable at cluster level
        cmd = enableOutOfBandManagementForCluster.enableOutOfBandManagementForClusterCmd()
        cmd.clusterid = host.clusterid
        response = self.apiclient.enableOutOfBandManagementForCluster(cmd)

        try:
            self.issuePowerActionCmd('STATUS')
            self.fail("Exception was expected, oobm is disabled at host level")
        except Exception: pass

        # Enable at host level
        cmd = enableOutOfBandManagementForHost.enableOutOfBandManagementForHostCmd()
        cmd.hostid = host.id
        response = self.apiclient.enableOutOfBandManagementForHost(cmd)

        response = self.issuePowerActionCmd('STATUS')
        self.assertEqual(response.powerstate, 'Off')


    def configureAndStartIpmiServer(self, power_state=None):
        """
            Setup ipmisim and enable out-of-band management for host
        """
        self.configureAndEnableOobm()
        self.startIpmiServer()
        if power_state:
            bmc = IpmiServerContext().bmc
            bmc.powerstate = power_state


    def assertIssueCommandState(self, command, expected):
        """
            Asserts power action result for a given power command
        """
        if command != 'STATUS':
            self.issuePowerActionCmd(command)
        response = self.issuePowerActionCmd('STATUS')
        self.assertEqual(response.powerstate, expected)


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_issue_power_status(self):
        """
            Tests out-of-band management issue power action
        """
        self.configureAndStartIpmiServer(power_state='on')
        self.assertIssueCommandState('STATUS', 'On')


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_issue_power_on(self):
        """
            Tests out-of-band management issue power on action
        """
        self.configureAndStartIpmiServer()
        self.assertIssueCommandState('ON', 'On')


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_issue_power_off(self):
        """
            Tests out-of-band management issue power off action
        """
        self.configureAndStartIpmiServer()
        self.assertIssueCommandState('OFF', 'Off')


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_issue_power_cycle(self):
        """
            Tests out-of-band management issue power cycle action
        """
        self.configureAndStartIpmiServer()
        self.assertIssueCommandState('CYCLE', 'On')


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_issue_power_reset(self):
        """
            Tests out-of-band management issue power reset action
        """
        self.configureAndStartIpmiServer()
        self.assertIssueCommandState('RESET', 'On')


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_issue_power_soft(self):
        """
            Tests out-of-band management issue power soft action
        """
        self.configureAndStartIpmiServer()
        self.assertIssueCommandState('SOFT', 'Off')


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_background_powerstate_sync(self):
        """
            Tests out-of-band management background powerstate sync
        """
        self.debug("Testing oobm background sync")
        interval = list_configurations(
            self.apiclient,
            name='outofbandmanagement.sync.interval'
        )[0].value

        self.configureAndEnableOobm()
        self.startIpmiServer()
        bmc = IpmiServerContext().bmc

        bmc.powerstate = 'on'
        self.checkSyncToState('On', interval)

        bmc.powerstate = 'off'
        self.checkSyncToState('Off', interval)

        self.server.shutdown()
        self.server.server_close()

        # Check for unknown state (ipmi server not reachable)
        self.checkSyncToState('Unknown', interval)


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_multiple_mgmt_server_ownership(self):
        """
            Tests out-of-band management ownership expiry across multi-mgmt server
        """
        self.configureAndEnableOobm()

        cloudstackVersion = Configurations.listCapabilities(self.apiclient).cloudstackversion

        currentMsHosts = []
        mshosts = self.dbclient.execute("select msid from mshost where version='%s' and removed is NULL and state='Up'" % (cloudstackVersion))
        if len(mshosts) > 0:
            currentMsHosts = map(lambda row: row[0], mshosts)

        # Inject fake ms host
        self.dbclient.execute("insert into mshost (msid,runid,name,state,version,service_ip,service_port,last_update) values (%s,%s,'oobm-marvin-fakebox', 'Down', '%s', '127.0.0.1', '22', NOW())" % (self.getFakeMsId(), self.getFakeMsRunId(), cloudstackVersion))

        # Pass ownership to the fake ms id
        self.dbclient.execute("update oobm set mgmt_server_id=%d where port=%d" % (self.getFakeMsId(), self.getIpmiServerPort()))

        self.debug("Testing oobm background sync")
        pingInterval = float(list_configurations(
            self.apiclient,
            name='ping.interval'
        )[0].value)

        pingTimeout = float(list_configurations(
            self.apiclient,
            name='ping.timeout'
        )[0].value)


        def removeFakeMgmtServer(fakeMsRunId):
            rows = self.dbclient.execute("select * from mshost_peer where peer_runid=%s" % fakeMsRunId)
            if len(rows) > 0:
                self.debug("Mgmt server is now trying to contact the fake mgmt server")
                self.dbclient.execute("update mshost set removed=now() where runid=%s" % fakeMsRunId)
                self.dbclient.execute("update mshost_peer set peer_state='Down' where peer_runid=%s" % fakeMsRunId)
                return True, None
            return False, None

        def checkOobmOwnershipExpiry(serverPort, fakeMsId):
            rows = self.dbclient.execute("select mgmt_server_id from oobm where port=%d" % (serverPort))
            if len(rows) > 0 and rows[0][0] != fakeMsId:
                self.debug("Out-of-band management ownership expired as node was detected to be gone")
                return True, None
            return False, None

        retry_interval = 1 + (pingInterval * pingTimeout / 10)

        res, _ = wait_until(retry_interval, 10, removeFakeMgmtServer, self.getFakeMsRunId())
        if not res:
            self.fail("Management server failed to turn down or remove fake mgmt server")

        res, _ = wait_until(retry_interval, 100, checkOobmOwnershipExpiry, self.getIpmiServerPort(), self.getFakeMsId())
        if not res:
            self.fail("Management server failed to expire ownership of fenced peer")

        self.debug("Testing oobm background sync should claim new ownership")
        interval = list_configurations(
            self.apiclient,
            name='outofbandmanagement.sync.interval'
        )[0].value

        self.startIpmiServer()
        bmc = IpmiServerContext().bmc
        bmc.powerstate = 'on'

        self.checkSyncToState('On', interval)

        result = self.dbclient.execute("select mgmt_server_id from oobm where port=%d" % (self.getIpmiServerPort()))
        newOwnerId = result[0][0]
        self.assertTrue(newOwnerId in currentMsHosts)


    @attr(tags = ["devcloud", "advanced", "advancedns", "smoke", "basic", "sg"], required_hardware="false")
    def test_oobm_zchange_password(self):
        """
            Tests out-of-band management change password feature
        """
        self.configureAndEnableOobm()
        self.startIpmiServer()

        self.debug("Testing oobm change password")

        alerts = Alert.list(self.apiclient, keyword="auth-error",
                        listall=True)
        alertCount = 0
        if alerts:
            alertCount = len(alerts)

        cmd = changeOutOfBandManagementPassword.changeOutOfBandManagementPasswordCmd()
        cmd.hostid = self.getHost().id
        cmd.password = "Password12345"
        try:
            response = self.apiclient.changeOutOfBandManagementPassword(cmd)
            self.assertEqual(response.status, True)
        except Exception as e:
            if "packet session id 0x0 does not match active session" in str(e):
                raise self.skipTest("Known ipmitool issue hit, skipping test")
            raise e

        bmc = IpmiServerContext().bmc
        bmc.powerstate = 'on'
        response = self.issuePowerActionCmd('STATUS')
        self.assertEqual(response.status, True)
        self.assertEqual(response.powerstate, 'On')

        # Reset configuration, resets password
        self.apiclient.configureOutOfBandManagement(self.getOobmConfigCmd())
        self.assertEqual(response.status, True)

        try:
            response = self.issuePowerActionCmd('STATUS')
            self.fail("Expected an exception to be thrown, failing")
        except Exception: pass

        alerts = Alert.list(self.apiclient, keyword="auth-error",
                        listall=True)

        self.assertTrue((len(alerts) - alertCount) >= 0)
