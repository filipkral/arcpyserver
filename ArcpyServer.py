"""GeoJSON server based on Python HTTP Simple Server and arcpy.
For details see docstring of ArcpyServerRequestHandler
and docstring of rows_to_geojson
"""

import os
import sys
import arcpy
import datetime
import json
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import urlparse

import mimetypes
mimetypes.init()

__version__ = 0.1

class Logfile:
    """Utility class for logging into a file"""
    def __init__(self, logfilepath):
        """Create a log file at logdilepath"""
        self.logpath = logfilepath
        """Path to the log file"""
        self.tform = "%Y-%m-%d %H-%M-%S"
        """Format for date time stamp"""
        self.tsep = ": "
        """Separator between time stamp and message"""
        self.br = " "
        """String to replace line breaks with when logging oneliners"""
    def log(self, msg, t=True, oneline=True):
        """Write a message into this log file
        msg -- message to log
        t -- include time stamp at the beginning
        oneline -- if True, all line breaks are removed before logging
        """
        with open(self.logpath, "a") as lf:
            if t:
                timenow = datetime.datetime.now()
                timestamp = datetime.datetime.strftime(timenow, self.tform)
                lf.write(timestamp)
                lf.write(self.tsep)
            if oneline:
                msg = msg.replace("\n", self.br)
            lf.write(msg)
            lf.write("\n")

def dict_lowerkeys(x):
    """Return a dictionary x where keys are lowercase.
    x is expected to have just one level (no nesting),
    and all keys of x are expected to be strings.
    """
    xlow = {}
    for k in x.iterkeys():
        xlow[str(k).lower()] = x[k]
    return xlow

def rows_to_geojson(in_fc, in_cols='*', in_wc='', returnGeometry=True, max_records=None):
    """Return features as dictionary that can be dumped to geojson.

    Requires ArcGIS 10.1 Service Pack 1 or higher.

    Returns FeatureCollection object as Python dictionary.

    If in_fc is a table with no geometry column or if returnGeometry is False,
    this function returns ObjectCollection where 'features' property is a list
    of {'type':'Object', 'properties': {...}}. This is a slight extension of
    the GeoJSON specification.

    in_fc = input feature class, table, etc
    in_cols = list of columns to select (no SHAPE@ !) or '','*',['*'] for all
    in_wc -- where clause to limit number of features returned
    returnGeometry -- Shape is returned if True (ingnored for tables)
    max_records -- maximum number of rows to return; default is None for all

    Example:
    >>> rows_to_geojson('c:\\temp\\tmp.shp')
    """

    if in_cols in ('*', '', ['*']):
        in_cols = []
        for f in arcpy.ListFields(in_fc):
            if f.type.lower() != 'geometry':
                in_cols.append(f.name)

    if max_records is None:
        max_records = int(arcpy.GetCount_management(in_fc).getOutput(0))

    d = arcpy.Describe(in_fc)
    shapeType = getattr(d, 'shapeType', 'Table')
    featureType = getattr(d, 'featureType', 'Object')
    sr = getattr(d, 'spatialReference', None)

    collection = {'type': 'FeatureCollection'}
    if shapeType == 'Table':
        collection = {'type': 'ObjectCollection'}
        featureType = "Object"
    elif returnGeometry == False:
        collection = {'type': 'ObjectCollection'}
        featureType = "Object"
    else:
        in_cols.insert(0, 'SHAPE@')
        featureType = "Feature"
        crs = None
        if sr is not None:
            crs = {'type': 'EPSG',
                    'properties': {'code': getattr(sr, 'factoryCode', 0)}
            }
        collection.update({'crs': crs})

    features = []
    i = 0
    with arcpy.da.SearchCursor(in_fc, in_cols, where_clause=in_wc) as sc:
        for row in sc:
            i += 1
            if i > max_records:
                if 'lg' in locals():
                    if isinstance(lg, Logfile):
                        lg.log(
                            "Maximum number of records reached with %s, %s" %
                            (in_fc, in_wc)
                        )
                break

            feature = {'type': featureType}
            props = dict(zip(in_cols,row))
            shp = props.pop('SHAPE@', None)
            if shp is not None:
                geom = {'geometry': shp.__geo_interface__}
                feature.update(geom)
            feature.update({'properties': props})
            features.append(feature)

    collection.update({'features': features})
    return collection


class ArcpyServerRequestHandler(BaseHTTPRequestHandler):
    """
    SERVER DEFINITION
    -----------------
    Handle request to a server and returns features GeoJSON etc.

    Required Python 2.7.x and ArcGIS Desktop 10.1 Service Pack 1 or higher.

    The server will respond to any request by trying to serve the requested
    resource as a mime type identified from (file) extension.

    Callback parameter and pretty json output have not been implemented.

    If the URL contains '/arcpyserver', the server will attempt to serve
    features as GeoJSON based on the query parameters:

    http://<hostname>/arcpyserver:<port>?dataset=<dataset>&cols=<cols>&where=<where>&bbox=<bbox>&geometries=<geometries>

    hostname -- name of the server or its IP; default is 127.0.0.1
    port -- port number; default is 8765
    dataset -- key to refer to a dataset (see Server Configuration below)
    cols -- (optional) comma separated list of columns to return, or *
    where -- (optional) where clause; only very simple clauses are supported!
    bbox -- (optional) bounding box to select features from; can be very slow!
        bbox must be in the same coodinate system as the underlying dataset!
        bbox is a comma separated list of xmin,ymin,xmax,ymax coordinates.
    geometries -- (optional) boolean indicating whether to return geometries;
        anything but ('False', 'FALSE', '0', 'false', 'f', 'F') is True
        if geometries is False, the returned json is 'type':'ObjectCollection'
        of objects of 'type':'Object' (slight extension of the GeoJSON specs)

    Example:
    http://127.0.0.1:8765/arcpyserver?dataset=cities&cols=name,admin
    http://127.0.0.1:8765/arcpyserver?dataset=cities&where=admin='Ukraine'
    http://127.0.0.1:8765/arcpyserver?dataset=cities&bbox=-2.0,50.0,2.0,60.0
    http://127.0.0.1:8765/arcpyserver?dataset=cities&cols=name&geometries=0

    SERVER CONFIGURATION
    --------------------

    wwwroot is a directory regarded as main directory for the application
    with all the content to be served. Should be where index.html resides.

    All feature classes exposed as service must be included in datasources
    dictionary. The key is then the <dataset> part of the URL.
    Feature classes do not need to reside under wwwroot.

    max_records -- limit of maximum number of records to return in a response
    TODO: implement bbox filter to return featured within the map extent.

    """
    # #############
    # SERVER CONFIG
    # #############
    # root folder
    #wwwroot = r'D:\gisroot\at201404052200_arcpyserver'
    wwwroot = os.path.dirname(os.path.realpath(__file__))
    # maximum number of records to return per request
    max_records = 1000
    # server side log file object
    lg = Logfile(os.path.join(wwwroot, 'arcpyserver.log'))
    # dictionary of registered datasources exposed through the server
    datasources = {
        'cities': os.path.join(wwwroot, 'data', 'populated_places.shp'),
        'countries': os.path.join(wwwroot, 'data', 'countries.shp')
    }
    # generic workspace
    arcpy.env.workspace = r'c:\\temp'

    def do_GET(self):
        p = self.path
        self.lg.log(p)

        in_lr = None
        try:
            """Hit arcpyserver"""
            if '/arcpyserver' in p.lower():

                #p = '/arcpyserver?dataset=test&where=1'
                # paste url for parameters
                print p
                pp = urlparse.urlparse(p)
                qry = urlparse.parse_qs(pp.query)
                qry = dict_lowerkeys(qry)

                dataset = qry.get('dataset', [None])[0]
                where = qry.get('where', [''])[0]
                cols = qry.get('cols', ['*'])[0]
                bbox = qry.get('bbox', [None])[0]
                geometries = qry.get('geometries', [True])[0]

                if str(geometries) in ('False', 'FALSE', '0', 'false', 'f', 'F'):
                    geometries = False

                self.lg.log(str((dataset, where, cols, bbox, geometries)))

                in_fc = self.datasources.get(dataset, None)

                if in_fc is None:
                    self.send_error(404, 'Datasource not found.')
                    return

                if bbox is not None:
                    bbox = [float(b.strip()) for b in bbox.split(",")]
                    bxmin,bymin,bxmax,bymax = bbox[0:4]
                    bpoly = arcpy.Polygon(arcpy.Array([
                        arcpy.Point(bxmin,bymin),
                        arcpy.Point(bxmin,bymax),
                        arcpy.Point(bxmax,bymax),
                        arcpy.Point(bxmax,bymin),
                        arcpy.Point(bxmin,bymin)
                    ]))

                    self.lg.log(str(bbox))

                    in_lr = 'lr' + datetime.datetime.now().strftime('%d%H%M%S%f')
                    self.lg.log(str((in_fc, in_lr)))
                    arcpy.management.MakeFeatureLayer(
                        in_fc,
                        in_lr
                    ).getOutput(0)

                    self.lg.log(str(in_lr))

                    in_fc = arcpy.management.SelectLayerByLocation(
                        in_lr, "INTERSECT", bpoly
                    ).getOutput(0)


                self.lg.log(str((
                    in_fc, cols.split(","),
                    where, geometries,
                    self.max_records, bbox
                )))

                try:
                    gjd = rows_to_geojson(
                        in_fc = in_fc,
                        in_cols = cols.split(","),
                        in_wc = where,
                        returnGeometry = geometries,
                        max_records = self.max_records
                    )
                except Exception as ex:
                    self.lg.log(ex.message)
                resp = json.dumps(gjd)

                #send status and headders
                self.send_response(200)
                self.send_header('Content-type','text-html')
                self.end_headers()
                # send result
                self.wfile.write(resp)
                return

            """Hit index"""
            if p in ('', '/', 'index.html', '/index.html'):
                with open(os.path.join(self.wwwroot, 'index.html'), 'r') as fl:
                    filecontent = fl.read()
                self.send_response(200)
                self.send_header('Content-type','text-html')
                self.end_headers()
                self.wfile.write(filecontent)
                return

            """Respond to favico request"""
            if p in ('/favicon.ico'):
                self.send_error(404, 'Favico not found.')

            """Else try to serve a page as html or other content"""
            fpath = os.path.join(self.wwwroot, p.strip("/").replace("/", "\\"))
            ext = fpath.split(".")[-1].lower()
            mime = mimetypes.types_map['.' + ext]
            self.lg.log(mime)
            with open(fpath, 'rb') as fl:
                filecontent = fl.read()
            self.send_response(200)
            self.send_header('Content-type', mime)
            self.end_headers()
            self.wfile.write(filecontent)
            return

        except Exception as ex:
            if in_lr is not None:
                try:
                    arcpy.Delete_management(in_lr)
                except:
                    pass
            if isinstance(ex, IOError):
                self.send_error(404, 'Page not found.')
            else:
                self.send_error(505, 'Internal server error.')

def run():
    print('http server is starting...')
    hostname,port = '127.0.0.1', 8765
    server_address = (hostname, port)
    httpd = HTTPServer(server_address, ArcpyServerRequestHandler)
    print('http server is running on %s:%s...' % (hostname, port))
    print('try http://127.0.0.1:8765/index.html')
    print('try http://127.0.0.1:8765/arcpyserver?dataset=cities')

    httpd.serve_forever()

if __name__ == '__main__':

    run()
    # to serve web pages, better run another web server by calling from cmd.exe
    # while in wwwroot of the arcapy server.
    # python -m SimpleHTTPServer 8080
    # then you can http://localhost:8080/

