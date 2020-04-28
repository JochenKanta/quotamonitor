# quotamonitor
Quotamonitor is a script that is used to check quotas across multiple different storage systems, notify owners of , and write quota information to CSV files and a mysql database. At this time it supports the following storage system vendors: 
* DellEMC Isilon
* Qumulo
* Vast Data
* Nexenta
* Racktop

and can be incorporated with Starfish in order to implement soft qutoas on a storage system that does not have inbuilt hard quotas. 

The script also supports checking the quota size of a mount via df. 

For the purposes of this script, quotas are assumed to be set on directories and are hard. At this time, the script does not support user or group quotas, or soft or advisory quotas. It could be modified to support these functions if desired. 

Quotamonitor has been tested on RHEL/CentOS/Scientific Linux 7.x and assumes a Linux operating system, although hopefully I've coded it in a way that it could be modified fairly easily to support Windows. 

Quotamonitor requires Python 2.7, as Qumulo does not yet support Python 3.x. 
