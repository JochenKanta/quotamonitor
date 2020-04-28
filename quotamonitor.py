#!/usr/bin/env python
import os
import sys 
import json
import smtplib
import argparse
from email.mime.text import MIMEText
import collections
import csv
import requests
import urllib3
import isi_sdk_8_1_0
from qumulo.rest_client import RestClient as qRestClient
import six.moves.urllib as urllib
from urllib import quote
from datetime import timedelta
from datetime import datetime
from time import time
from string import Template
import MySQLdb as mdb

urllib3.disable_warnings()

# Size Definitions
KILOBYTE = 1024
MEGABYTE = 1024 * KILOBYTE
GIGABYTE = 1024 * MEGABYTE
TERABYTE = 1024 * GIGABYTE

# Decode Functions
def _decode_list(data):
    rv = []
    for item in data:
        if isinstance(item, unicode):
            item = item.encode('utf-8')
        elif isinstance(item, list):
            item = _decode_list(item)
        elif isinstance(item, dict):
            item = _decode_dict(item)
        rv.append(item)
    return rv

def _decode_dict(data):
    rv = {}
    for key, value in data.iteritems():
        if isinstance(key, unicode):
            key = key.encode('utf-8')
        if isinstance(value, unicode):
            value = value.encode('utf-8')
        elif isinstance(value, list):
            value = _decode_list(value)
        elif isinstance(value, dict):
            value = _decode_dict(value)
        rv[key] = value
    return rv

### Storage Class Definitions ###

# Qumulo
class q_api:
    def __init__(self, name, qconfig):
        self.systemname = name
        self.user = qconfig['user']
        self.password = qconfig['password']
        self.host = qconfig['url']
        self.port = qconfig['port']
        self.logfile = qconfig['logfile']
        self.nfsmapping = qconfig['nfsmapping']
        
    def login(self):
        '''Obtain credentials from the REST server'''
        try:
            self.rc = qRestClient(self.host, self.port)
            self.rc.login(self.user, self.password)
        except Exception as excpt:
            print("Error connecting to the REST server: {}".format(excpt))
            print(__doc__)
            pass

    def get_free_space(self):
        fs_stats = self.rc.fs.read_fs_stats()
        self.freesize = int(fs_stats['free_size_bytes'])
        self.totalsize = int(fs_stats['total_size_bytes'])

    def get_all_quotas(self):
        try:
            all_quotas_raw = self.rc.quota.get_all_quotas_with_status(10000)
            self.quotalist = _decode_list(list(all_quotas_raw)[0]['quotas'])
        except Exception as excpt:
            print("An error occurred contacting the storage for the quota list: {}".format(excpt))
            sys.exit(1)
    
    def get_total_files(self, toppath):
        #This currently only works with the admin username and password. 
        fs_stats = self.rc.fs.read_dir_aggregates(toppath)
        total_files = int(fs_stats['total_files'])
        return total_files
    
    def process_quotas(self):
        self.login()
        self.get_free_space()
        self.get_all_quotas()
        self.quotadict = {}
        for quota in self.quotalist:
            lab, nfspath, application = translate_path(quota['path'], self.systemname, self.nfsmapping)
            if nfspath is None:
                continue
            if application is not '':
                lab = '{}--{}'.format(lab, application)
            self.quotadict[lab]={
                'usage':int(quota['capacity_usage']),
                'quota':int(quota['limit']),
                'total_files':self.get_total_files(quota['path']),
                'total_files':0,
                'nfspath':nfspath,
                'special':application
                }
        self.quotadict['FREE'] = {'freesize':self.freesize,'totalsize':self.totalsize}

            
# Vast		
class v_api:
    def __init__(self, name, vconfig):
        self.systemname = name
        self.user = vconfig['user']
        self.password = vconfig['password']
        self.host = vconfig['url']
        self.logfile = vconfig['logfile']
        self.nfsmapping = vconfig['nfsmapping']
        
    def get_data(self, vobj):
        try:
            s = requests.session()
            data = s.get('https://{}/api/{}/'.format(self.host, vobj), auth=(self.user, self.password), verify=False)
            return data.json()
        except Exception as excpt:
            print("Error connecting to the REST server: {}".format(excpt))
            print(__doc__)
            sys.exit(1)
            
    def get_free_space(self):
        inuse = int(self.get_data('clusters')[0]["logical_space_in_use"])
        self.totalsize = int(self.get_data('clusters')[0]["logical_space"])
        self.freesize = self.totalsize - inuse

    def get_all_quotas(self):
        self.quotalist = _decode_list(self.get_data('quotas'))
    
    def process_quotas(self):
        self.get_free_space()
        self.get_all_quotas()
        self.quotadict = {}
        for quota in self.quotalist:
            lab, nfspath, application = translate_path(quota['path'], self.systemname, self.nfsmapping)
            if nfspath is None:
                continue
            if application is not '':
                lab = '{}--{}'.format(lab, application)
            self.quotadict[lab]={
                'usage':int(quota['used_capacity']),
                'quota':int(quota['hard_limit']),
                'total_files':int(quota['used_inodes']),
                'nfspath':nfspath,
                'special':application
                }
        self.quotadict['FREE'] = {'freesize':self.freesize,'totalsize':self.totalsize}

# Isilon
class i_api:
    def __init__(self, name, iconfig):
        self.systemname = name
        self.user = iconfig['user']
        self.password = iconfig['password']
        self.host = iconfig['url']
        self.logfile = iconfig['logfile']
        self.nfsmapping = iconfig['nfsmapping']
        
    def login(self):
        # configure cluster connection: basicAuth
        configuration = isi_sdk_8_1_0.Configuration()
        configuration.host = 'https://{}:8080'.format(self.host)
        configuration.username = self.user
        configuration.password = self.password
        configuration.verify_ssl = False
        # create an instance of the API class
        api_client = isi_sdk_8_1_0.ApiClient(configuration)
        self.cluster_api = isi_sdk_8_1_0.ClusterApi(api_client)
        self.quota_api = isi_sdk_8_1_0.QuotaApi(api_client)
        
    def get_free_space(self):
        clusterinfo = self.cluster_api.get_cluster_statfs()
        self.totalsize = clusterinfo.f_blocks * clusterinfo.f_bsize
        self.freesize = clusterinfo.f_bavail * clusterinfo.f_bsize
    
    def get_all_quotas(self):
        self.quotalist = self.quota_api.list_quota_quotas().to_dict()['quotas']
        
    def process_quotas(self):
        self.login()
        self.get_free_space()
        self.get_all_quotas()
        self.quotadict = {}
        for quota in self.quotalist:
            toppath = quota['path']
            lab, nfspath, application = translate_path(toppath, self.systemname, self.nfsmapping)
            if nfspath is None:
              continue
            if application is not '':
                lab = '{}--{}'.format(lab, application)
            self.quotadict[lab]={
                'usage':int(quota['usage']['logical']),
                'quota':int(quota['thresholds']['hard']),
                'total_files':int(quota['usage']['inodes']),
                'nfspath':nfspath,
                'special':application
                }
        self.quotadict['FREE'] = {'freesize':self.freesize,'totalsize':self.totalsize}

# Racktop
class r_api:
    def __init__(self, name, rconfig):
        self.systemname = name
        self.user = rconfig['user']
        self.password = rconfig['password']
        self.host = rconfig['url']
        self.dataset = rconfig['dataset']
        self.logfile = rconfig['logfile']
        self.nfsmapping = rconfig['nfsmapping']
        
    def login(self):
        self.headers = {'Content-Type': 'application/json'}
        response = requests.post(
            "https://{}:8443/login".format(self.host),
            headers=self.headers,
            auth=(self.user, self.password), 
            verify=False
            )
        self.headers['Authorization'] = "Bearer {}".format(response.json()['token'])
    
    def get_all_quotas(self):
        self.quotalist = []
        self.headers['User-Agent'] = "BsrCli"
        urltoget = "https://{}:8443/internal/v1/zfs/datasets?dataset={}&types=all&props=refquota,usedbydataset&offset=1".format(self.host, self.dataset)
        response = requests.get(urltoget, headers=self.headers, verify=False)
        if response.status_code != 200:
            print("invalid auth response")
            print(response.request)
            print(response.reason)
        datasets_raw = _decode_dict(response.json())['Datasets']
        for dataset in datasets_raw:
            self.quotalist.append({
                'toppath':'/' + dataset['Path'],
                'refquota':dataset['Properties'][0]['Value'], 
                'used':dataset['Properties'][1]['Value']
                })
    
    def get_free(self):
        self.headers['User-Agent'] = "BsrCli"
        volume = self.dataset.split('/')[0]
        urltoget = "https://{}:8443/internal/v1/zfs/dataset?dataset={}".format(self.host, volume)
        response = requests.get(urltoget, headers=self.headers, verify=False)
        free_raw = _decode_dict(response.json())['Dataset']
        self.freesize = [property for property in free_raw['Properties'] if property['Name'] == 'available'][0]['Value']
        used = [property for property in free_raw['Properties'] if property['Name'] == 'used'][0]['Value']
        self.totalsize = int(self.freesize) + int(used)
      
                                    
    def process_quotas(self):
        self.login()
        self.get_all_quotas()
        self.get_free()
        self.quotadict = {}
        for quota in self.quotalist:
            lab, nfspath, application = translate_path(quota['toppath'], self.systemname, self.nfsmapping)
            if nfspath is None:
              continue
            if application is not '':
                lab = '{}--{}'.format(lab, application)
            self.quotadict[lab]={
                'usage':int(quota['used']),
                'quota':int(quota['refquota']),
                'total_files':0,
                'nfspath':nfspath,
                'special':application
                }
        self.quotadict['FREE'] = {'freesize':self.freesize,'totalsize':self.totalsize}

#Nexenta 5

class n_api:
    def __init__(self, name, nconfig):
        self.systemname = name
        self.user = nconfig['user']
        self.password = nconfig['password']
        self.host = nconfig['url']
        self.toplevel = nconfig['toplevel']
        self.logfile = nconfig['logfile']
        self.nfsmapping = nconfig['nfsmapping']
        
    def login(self):
        self.headers = {'Content-Type': 'application/json'}
        auth_params = {"username": self.user, "password": self.password}
        response = requests.post(
            "https://{}:8443/auth/login".format(self.host),
            headers=self.headers,
            data=json.dumps(auth_params),
            verify=False
            )
        if response.status_code != 200:
            print("invalid auth response")
            print(response.request)
            print(response.reason)
        self.headers['Authorization'] = "Bearer {}".format(response.json()['token'])
    
    def get_all_quotas(self):
        self.quotalist = []
        urltoget = "https://{}:8443/storage/filesystems".format(self.host)
        response = requests.get(urltoget, headers=self.headers, verify=False)
        if response.status_code != 200:
            print("invalid auth response")
            print(response.request)
            print(response.reason)
        datasets_raw = _decode_dict(response.json())['data']
        topinfo = datasets_raw.pop(0)
        self.freesize = topinfo['bytesAvailable']
        used = topinfo['bytesUsed']
        self.totalsize = self.freesize + used
        for dataset in datasets_raw:
            name = dataset['name']
            self.quotalist.append({
                'toppath':'/{}'.format(dataset['path']),
                'refquota':self.get_refquota(name),
                'used':dataset['bytesReferenced']
            })

    def get_refquota(self, name):
        urltoget = "https://{}:8443/storage/filesystems/{}%2F{}".format(self.host, self.toplevel, name)
        response = requests.get(urltoget, headers=self.headers, verify=False)
        rawdata = _decode_dict(response.json())
        refquota = rawdata['referencedQuotaSize']
        return refquota
      
    def process_quotas(self):
        self.login()
        self.get_all_quotas()
        self.quotadict = {}
        for quota in self.quotalist:
            lab, nfspath, application = translate_path(quota['toppath'], self.systemname, self.nfsmapping)
            if nfspath is None:
              continue
            if application is not '':
                lab = '{}--{}'.format(lab, application)
            self.quotadict[lab]={
                'usage':int(quota['used']),
                'quota':int(quota['refquota']),
                'total_files':0,
                'nfspath':nfspath,
                'special':application
                }
        self.quotadict['FREE'] = {'freesize':self.freesize,'totalsize':self.totalsize}

# Starfish
class sf_api:
    def __init__(self, sfconfig):
        self.user = sfconfig['user']
        self.password = sfconfig['password']
        self.host = sfconfig['url']
        self.logfile = sfconfig['logfile']
        self.response = {}
        
    def login(self):
        auth_params = {"username": self.user, "password": self.password}
        self.headers = {'Content-Type': 'application/json'}
        response = requests.post(
            "https://{}/api/auth/".format(self.host), 
            data=json.dumps(auth_params), 
            headers=self.headers, 
            verify=False
            )
        if response.status_code != 200:
            print("invalid auth response")
            print(response.request)
            print(response.reason)
        else:
            token = response.json()['token']
            self.headers['Authorization'] = "Bearer {}".format(token)
    
    def getquota(self, vol_path):
        volencoded = quote(vol_path, safe=':')
        sf_response = requests.get(
            "https://{}/api/query/{}/?query=depth=0&type=d&format=rec_aggrs&output_format=json".format(self.host, volencoded), 
            headers=self.headers, 
            verify=False
            ).json()
        if len(sf_response) == 1:
            return _decode_dict(sf_response[0])
        else:
            print("Exception on vol_path, multiple items returned")
    
    def get_all_quotas(self, volpathlimits):
        self.sfquotadict = {}
        for vol_path,limit in volpathlimits.iteritems():
            self.sfquotadict[vol_path] = {'sfdata':self.getquota(vol_path), 'limit':limit}
        
    def process_quotas(self):
        self.softquotadict = {}
        for volpath, sfquota in self.sfquotadict.iteritems():
            storage, path = volpath.split(':')
            if storage not in self.softquotadict.keys():
                self.softquotadict[storage] = {}
            rawpath = '/{}/{}'.format(storage,path)
            lab, nfspath, application = translate_path(rawpath, storage, self.nfsmapping)
            if nfspath is None:
                continue
            if application is not '':
                lab = '{}--{}'.format(lab, application)
            self.softquotadict[storage][lab]={
                'usage':int(sfquota['sfdata']['rec_aggrs']['size']),
                'total_files':int(sfquota['sfdata']['rec_aggrs']['files']) + int(sfquota['sfdata']['rec_aggrs']['dirs']),
                'quota':int(sfquota['limit'] * TERABYTE),
                'nfspath':nfspath,
                'special':'soft'
                }

# Mounted storage w/o API
class df_system:
    def __init__(self, name, dfconfig):
        self.systemname = name
        self.mountpath = dfconfig['mountpath']
        self.logfile = dfconfig['logfile']
        self.nfsmapping = dfconfig['nfsmapping']
    
    def get_mounts(self):
        self.mounts = [line.split()[1] for line in open("/etc/mtab")
                      if line.split()[0].startswith(self.mountpath)]
        
    def get_all_quotas(self):
        self.quotalist = []
        for mount in self.mounts:
            rawusage = os.popen("df {}".format(mount))
            line = rawusage.readlines()[-1]
            self.quotalist.append(line.split())
            
    def process_quotas(self):
        self.get_mounts()
        self.get_all_quotas()
        self.quotadict = {}
        for quota in self.quotalist:
            toppath = quota[5]
            lab, nfspath, application = translate_path(toppath, self.systemname, self.nfsmapping)
            if nfspath is None:
                continue
            if application is not '':
                lab = '{}--{}'.format(lab, application)
            self.quotadict[lab]={
                'usage':int(quota[2]) * KILOBYTE,
                'quota':int(quota[1]) * KILOBYTE,
                'total_files':0,
                'nfspath':nfspath,
                'special':application
                }
        
# Catch all
class unlisted_storage:
    def __init__(self, name):
        self.systemname = name
        self.logfile = '/dev/null'
        self.quotadict = {}


### Info Gathering and Parsing Functions
def getconfig(configpath):
    configdict = {}
    custom_mapping = {}
    try:
        with open (configpath, 'r') as j:
            config = json.load(j, object_hook=_decode_dict)

        for storagename in (
                storagename for storagename in config['storagesystems'].keys() if 'qumulo' in config['storagesystems'][storagename]['type']
            ):
            config['storagesystems'][storagename]['port'] = 8000

        group_dict = {}
        for lab,lab_info in config['groups'].iteritems():
            group_dict[lab] = lab_info
            if 'custom_mapping' in lab_info.keys():
                for storagesystem,cmname in lab_info['custom_mapping'].iteritems():
                    if cmname not in custom_mapping.keys():
                        custom_mapping[cmname] = {}
                    custom_mapping[cmname][storagesystem] = lab

    except Exception as excpt:
        print("Improperly formatted {} or missing file: ".format(configpath))
        print(excpt)
        sys.exit(1)
        
    return config, group_dict, custom_mapping

def translate_path(toppath, systemname, nfsmapping):
    lab = None
    nfspath = None
    application = ''
    for mapping in (mapping for mapping in nfsmapping.keys() if mapping in toppath):
        lab = os.path.relpath(toppath, mapping)
        if lab is '.':
            try:
                quotaname = os.path.basename(os.path.normpath(toppath))
                lab = groupdict[quotaname]['custom_name'][systemname]
            except:
                print('Mapping not found for {}'.format(toppath))
                continue
        nfspath = os.path.join(nfsmapping[mapping], lab)
    
    if lab in custom_mapping.keys() and systemname in custom_mapping[lab].keys():
        lab = custom_mapping[lab][systemname]

    if lab in groupdict.keys():
        labdict = groupdict[lab]
        if 'custom_name' in labdict.keys() and systemname in labdict['custom_name'].keys():
            nfspath = os.path.join(os.path.dirname(nfspath), labdict['custom_name'][systemname])

    for app in configdict['application_shares'].keys():
        prefix = configdict['application_shares'][app]['storageprefix']
        if prefix.get(systemname, 'NONE') in toppath:
            application = app
    return lab, nfspath, application


def buildsystemdict():
    systemdict = {}
    for systemname, config in configdict['storagesystems'].iteritems():
        if 'vast' in config['type']:
            systemdict[systemname] = v_api(systemname, config)
            systemdict[systemname].process_quotas()
        
        elif 'qumulo' in config['type']:
            systemdict[systemname] = q_api(systemname, config)
            systemdict[systemname].process_quotas()
    
        elif 'isilon' in config['type']:
            systemdict[systemname] = i_api(systemname, config)
            systemdict[systemname].process_quotas()

        elif 'racktop' in config['type']:
            systemdict[systemname] = r_api(systemname, config)
            systemdict[systemname].process_quotas()
            
        elif 'nexenta' in config['type']:
            systemdict[systemname] = n_api(systemname, config)
            systemdict[systemname].process_quotas()

        elif 'generic' in config['type']:
            systemdict[systemname] = df_system(systemname, config)
            systemdict[systemname].process_quotas()
        
    get_soft_quotas()

    return systemdict

def get_soft_quotas():
    starfish = sf_api(configdict['storagesystems']['starfish'])
    starfish.login()
    volpathlimits = {}
    for group in (group for group in groupdict.keys() if 'soft_quota' in groupdict[group].keys()):
        for storage, limit in groupdict[group]['soft_quota'].iteritems():
            vol_path = "{}:{}".format(storage, group)
            volpathlimits[vol_path] = limit
    starfish.get_all_quotas(volpathlimits)
    starfish.process_quotas()
    for volume, entry in starfish.softquotadict.iteritems():
        if volume not in systemdict.keys():
            systemdict[volume] = unlisted_storage(volume)
        for lab, quota in entry.iteritems():
            systemdict[volume].quotadict[lab] = quota

### LogFile Functions ###

def buildloglist():
    loglist = {}
    
    for system, obj in systemdict.iteritems():
        freelist = []
        loglist[system] = []
        for lab, linfo in obj.quotadict.iteritems():
            try:
                if lab == 'FREE':
                    freelist = ['FREE', linfo['freesize'], linfo['totalsize']]
                    continue
                lab = lab.replace('--{}'.format(linfo['special']),'')
                loglist[system].append([lab, linfo['usage'], linfo['quota'], linfo['total_files'], linfo['special']])
            except Exception as excpt:
                print("Could not build list for {} on {}".format(lab, system))
                print linfo
                print excpt
        loglist[system].sort()
        loglist[system].insert(0,freelist)
    return loglist

def writecsvs(loglist):
    for system in loglist.keys():
        try:
            with open (systemdict[system].logfile,'w') as f:
                header = 'Lab,SpaceUsed,TotalSpace,TotalFile'
                f.write(header + '\n')
                csv_writer = csv.writer(f)
                csv_writer.writerows(loglist[system])
        except Exception as excpt:
            print("Unable to write log file for {}".format(system))
            print excpt

### Email Functions ###

def process_emails(default_recipient, default_alert_percent):
    maillist = []
    for system, obj in systemdict.iteritems():
        for lab, linfo in obj.quotadict.iteritems():
            recipient = []
            recipient.extend(default_recipient)
            if 'FREE' in lab:
                continue
            elif linfo['special'] is not '':
                groupkey = lab.replace('--{}'.format(linfo['special']),'')
            else:
                groupkey = lab

            if linfo['special'] not in ('', 'soft'):
                recipient.extend(configdict['application_shares'][linfo['special']]['addmail'])
                
            labdict = obj.quotadict[lab]
            if groupkey in groupdict.keys():
                labconfig = groupdict[groupkey]
                emailtype, checkfile, percentage = check_percentage(system, lab, labdict, labconfig['warn_percent'], configdict['application_shares'])
                recipient.extend(labconfig['mail_to'])
            elif labdict['quota'] != 0:
                emailtype, checkfile, percentage = check_percentage(system, lab, labdict, default_alert_percent, configdict['application_shares'])

            if emailtype is not '':
                if not os.path.isfile(checkfile):
                    with open(checkfile,"a+") as f:
                        pass
                    maillist.append({
                        'nfspath':labdict['nfspath'], 
                        'system':system, 
                        'quotaname':lab, 
                        'usage':'{:.2f}'.format(float(labdict['usage']) / TERABYTE), 
                        'quota':'{:.2f}'.format(float(labdict['quota']) / TERABYTE), 
                        'mailto':recipient,
                        'mailtype':emailtype,
                        'percentage':percentage,
                        'special':labdict['special']
                        })

    return maillist
        
def check_percentage(system, lab, labdict, warn_percent, application_shares):
    percentage = 100 * labdict['usage'] / labdict['quota']
    checkfileroot = os.path.join('/tmp', '{}{}-{}-t'.format(labdict['special'], system, os.path.basename(lab)))
    emailtype = ''
    checkfile = ''
    fullcheckfile = checkfileroot + '-full'
    warncheckfile = checkfileroot + '-warn'
    # pass age limits in days
    cleanupfiles(fullcheckfile, 1)
    cleanupfiles(warncheckfile, 7)

    if labdict['special'] in application_shares.keys():
        warn_percent = application_shares[labdict['special']]['warn_percent']
        full_percent = application_shares[labdict['special']]['full_percent']
    else:
        full_percent = 100

    if percentage >= warn_percent and percentage < full_percent:
        emailtype = 'warn'
        checkfile = warncheckfile
    elif percentage >= full_percent:
        checkfile = fullcheckfile
        emailtype = 'full'
        try:
            os.remove(warncheckfile)
        except:
            pass
    else:
        try:
            os.remove(warncheckfile)
            os.remove(fullcheckfile)
        except:
            pass
    return emailtype, checkfile, percentage

def cleanupfiles(checkfile, daysback):
    delta = timedelta(days=daysback).total_seconds()
    age = time() - int(delta)
    try:
        if age > os.path.getmtime(checkfile):
            os.remove(checkfile)
    except:
        pass

def read_template(filename, template_path):
    filepath = os.path.join(template_path, filename)
    with open(filepath, 'r') as template_file:
        template = template_file.read()
    return Template(template)

def buildmail(maildict, template_path, default_subject):
    mailtype = maildict['mailtype']
    special = maildict['special']
    labname = maildict['quotaname'].split('-')[0].capitalize()

    template = '{}{}.txt'.format(special, mailtype)

    if special not in ('', 'soft'): 
        subject = configdict['application_shares'][special]['subject'][mailtype]

    else:
       subject = default_subject[mailtype].format(labname, maildict['system'])

    body = read_template(template, template_path).substitute(
        LABNAME=labname,
        STORAGE=maildict['system'],
        NFSPATH=maildict['nfspath'],
        PERCENTAGE=maildict['percentage'], 
        USAGE=maildict['usage'], 
        QUOTA=maildict['quota']
        )
    return subject, body

def send_mail(email_settings, subject, body, recipients):
    try:
        mmsg = MIMEText(body, 'html')
        mmsg['Subject'] = subject
        mmsg['From'] = email_settings['sender_address']
        mmsg['To'] = ", ".join(recipients)
#        print(mmsg)
        
        session = smtplib.SMTP(email_settings['smtp_server'])
        session.sendmail(email_settings['sender_address'], recipients, mmsg.as_string())
        session.quit()
    except Exception,excpt:
        print('Exception in sending mail to {}'.format(recipients))
        print(excpt)

def sendalerts(email_settings):
    maillist = process_emails(email_settings['default_recipient'], email_settings['default_alert_percent'])
    for maildict in maillist:
        subject, body = buildmail(maildict, email_settings['template_path'], email_settings['subject'])
        send_mail(email_settings, subject, body, maildict['mailto'])

#Database functions

def getinfofromdb(qdb, command):
    qdb.execute(command)
    result = qdb.fetchall()
    return result

def createinsertion(loglist, dbmap):
    dbcon, qdb = connect_to_db()
    holdingdict = {}
    #insertiondict = {}
    currdate = datetime.fromtimestamp(time())
    for system in loglist.keys():
        tier = dbmap[system]
        if tier not in holdingdict.keys():
            holdingdict[tier] = {}
        for lab in (lab for lab in loglist[system] if len(lab) is not 0 and 'FREE' not in lab[0]):
            if lab[4] not in ('', 'soft'):
                labname = '{}-{}'.format(lab[0], lab[4])
            else:
                labname = lab[0]
            used = lab[1]
            quota = lab[2]

            try:
                mapid = getinfofromdb(qdb, 'SELECT Id FROM Maps WHERE Name="{}"'.format(lab[0]))[0][0]
            except Exception, excpt:
                print('mapping not found: {} {}'.format(labname, excpt))
                continue
            if getinfofromdb(qdb, 'SELECT * FROM {} where Path = "{}" AND Date = "{}"'.format(tier, 
                            labname, currdate.date())) is ():
                if labname not in holdingdict[tier].keys():
                    holdingdict[tier][labname] = {'date':currdate.date(), 'used':used, 'quota':quota, 'mapping':mapid}
                else:
                    totquot = holdingdict[tier][labname]['quota'] + quota
                    totused = holdingdict[tier][labname]['used'] + used
                    holdingdict[tier][labname]['quota'] = totquot
                    holdingdict[tier][labname]['used'] = totused
    #    for line in holdingdict[tier]:
    #        print(line, holdingdict[tier][line])
    insertintotable(holdingdict, qdb, dbcon)

def insertintotable(holdingdict, qdb, dbcon):
    for tier in (tier for tier in holdingdict.keys() if holdingdict[tier]):
        insertionlist = []
        for lab, insdict in holdingdict[tier].iteritems():
            insertionlist.append((insdict['date'], lab, insdict['used'], insdict['quota'], insdict['mapping']))
        sql = "INSERT INTO {} (Date, Path, Used, Hard, Map) VALUES (%s, %s, %s, %s, %s)".format(tier)
        qdb.executemany(sql, insertionlist)
    dbcon.commit()

def connect_to_db():
    dbcon = mdb.connect(
                        configdict['db_settings']['host'],
                        configdict['db_settings']['user'], 
                        configdict['db_settings']['password'],
                        configdict['db_settings']['database']
                       )
    return dbcon, dbcon.cursor()

### Main ###

def main(argv):
    ### Edit the configpath for the location of your config.json ### 
    configpath = "./config.json"
    ################################################################

    parser = argparse.ArgumentParser('Check quota status, create log file, and email if over quota')
    parser.add_argument('-c', '--config', type=str, default=configpath, required=False)
    args = parser.parse_args()
    configpath = args.config

    global groupdict
    global configdict
    global systemdict
    global custom_mapping
    configdict, groupdict, custom_mapping = getconfig(configpath)
    systemdict = buildsystemdict()
    loglist = buildloglist()
    writecsvs(loglist)
    sendalerts(configdict['email_settings'])
    createinsertion(loglist, configdict['db_settings']['map'])

if __name__ == '__main__':
	main(sys.argv[1:])
