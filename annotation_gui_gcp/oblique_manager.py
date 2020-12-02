from opensfm.features import denormalized_image_coordinates
from opensfm.dataset import DataSet
from rtree import index
from PIL import Image
from opensfm import dataset
from matplotlib.image import _rgb_to_rgba
import numpy as np
import sys
from pathlib import Path
import multiprocessing
import os

import numpy as np
from matplotlib.image import _rgb_to_rgba
from opensfm import dataset
from PIL import Image
from rtree import index

from opensfm.dataset import DataSet
from export_reconstruction_points import world_points

IMAGE_MAX_SIZE = 2000


def load_image(in_tuple, win=int(IMAGE_MAX_SIZE/2)):
    '''
    Load an image around a pixel location

    Inputs
    ------
    in_tuple : tuple
         (path, px, py)
         path: str, px: int, py:int
    '''

    # package for pool
    path, px, py = in_tuple
    rgb = Image.open(path)

    y1 = np.max([py-win, 0])
    y2 = np.min([py+win, rgb.height])
    x1 = np.max([px-win, 0])
    x2 = np.min([px+win, rgb.width])

    # use this to mark feature point?
    # will need to back out original px, py after click
    pt_x = np.min([px, win])
    pt_y = np.min([py, win])

    if win is not None:
        rgb = rgb.crop((x1, y1, x2, y2))

    # Matplotlib will transform to rgba when plotting
    return _rgb_to_rgba(np.asarray(rgb))


def get_distance(lat1, lon1, alt1, lat2, lon2, alt2):
    ecef_str = 'epsg:4978'
    ll_str = 'epsg:4326'
    ecef_trans = Transformer.from_proj(Proj(ll_str), Proj(ecef_str))
    x1, y1, z1 = ecef_trans.transform(lat1, lon1, alt1)
    x2, y2, z2 = ecef_trans.transform(lat2, lon2, alt2)
    distance = np.sqrt((x1-x2)**2+(y1-y2)**2+(z1-z2)**2)
    return distance


class ObliqueManager:
    def __init__(self, path: str, preload_images=True):
        self.path = Path(path)
        self.rtree_path=f'{self.path}/rtree_index'
        self.image_cache = {}
        self.image_coord = {}
        self.candidate_images = []
        self.preload_bol = preload_images
        self.get_rtree_index()

    def image_path(self, image_name):
        return f"{self.path}/images/{image_name}"

    def get_image(self, image_name):
        if image_name not in self.image_cache:
            path = self.image_path(image_name)
            px = image_name.split('_')[-2]
            py = image_name.split('_')[-1]
            self.image_cache[image_name] = load_image((path, px, py))
        return self.image_cache[image_name]

    def load_latlons(self):
        # 'canonical' latlon not as useful for obliques
        return {}

    def get_candidates(self, lat: float, lon: float):
        """
        Given a lat lon alt, find prospective oblique images
        TODO: add alt as arg, make 3d
        """
        if lat is None or lon is None:
            return []

        aerial_match = self.aerial_idx.nearest(
            (lon, lat), objects=True)

        self.aerial_matches = [x.object['images'] for x in aerial_match][0]
        self.image_names = [x['image_name']
                            for x in self.aerial_matches]
        print(f"Found {len(self.aerial_matches)} aerial images")

        if self.preload_bol:
            self.preload_images()

        return self.image_names

    def get_rtree_index(self):

        if os.path.exists(f'{self.rtree_path}.dat'):
            self.aerial_idx = index.Index(self.rtree_path)
        else:
            self.build_rtree_index()
            self.aerial_idx = index.Index(self.rtree_path)
            
    def build_rtree_index(self):
        print("building oblique SfM rtree...")

        ds = DataSet(self.path)
        data = world_points(ds)
        aerial_keypoints = []
        p = index.Property()
        p.dimensions = 2
        aerial_idx = index.Index(self.rtree_path, properties=p)
        for i, (key, val) in enumerate(data.items()):
            images = val['images']
            ims = []
            for im in images:
                xpx = int(np.round(im['x_px']))
                ypx = int(np.round(im['y_px']))
                imn = {'x_px_int': xpx,
                       'y_px_int': ypx,
                       'image_name': f"{im['image_id']}_{xpx}_{ypx}"}
                ims.append(dict(im, **imn))
            lat = val['location']['lat']
            lon = val['location']['lon']
            alt = val['location']['alt']
            pt = {'key': key, 'lat': lat, 'lon': lon,
                  'alt': alt, 'images': ims}
            aerial_keypoints.append(pt)
            aerial_idx.insert(i, (lon, lat), obj=pt)

        aerial_idx.close()
        
    def preload_images(self):
        n_cpu = multiprocessing.cpu_count()
        print(f"Preloading images with {n_cpu} processes")
        paths = []
        image_names = []
        for match in self.aerial_matches:
            image_names.append(match['image_name'])
            paths.append(
                (self.image_path(match['image_id']), match['x_px_int'], (match['y_px_int'])))
        pool = multiprocessing.Pool(processes=n_cpu)
        images = pool.map(load_image, paths)
        for image_name, im, path in zip(image_names, images, paths):
            self.image_cache[image_name] = im
            self.image_coord[image_name] = (path[1:])

    def get_image_size(self, image_name):
        return self.get_image(image_name).shape[:2]

    def get_offsets(self, image_name):
        px, py = self.image_coord[image_name]
        height, width = self.get_image_size(image_name)
        win = int(IMAGE_MAX_SIZE/2)
        y1 = np.max([py-win, 0])
        x1 = np.max([px-win, 0])
        return x1, y1

    def get_nearest_feature(self, image_name, x, y):
        return None