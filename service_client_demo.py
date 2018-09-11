import redis
import cv2
import uuid
import time
import queue
import threading
import numpy as np

import vision_config
import manage_data
from TrackingFace import MultiTracker
from interact_database_v2 import Database
from object_DL import Person, Camera, Location, Image
import logging

class ClientService:
    class FLAG:
        def __init__(self):
            self.RUNNING = True
            self.SUBSCRIBED = False
    def __init__(self, database, camera):
        self.__FLAGS = ClientService.FLAG()
        self.database = database
        self.camera = camera
        self.capture = None
        self.subscribed_server_info = {}
        self.cid = str(uuid.uuid4())
        self.detect_service_line = redis.StrictRedis(host='localhost', port=6379)
        self.identify_service_line = redis.StrictRedis(host='localhost', port=6379)
        self.subscribe_object = self.detect_service_line.pubsub()
        if self.camera is not None:
            self.capture = cv2.VideoCapture(camera.httpurl)
            logging.info("Video Capture device is camera [ id: {} - name: {} - httpurl: {} - rstpurl: {} - location: {}]".format(\
                                                    camera.id, camera.cameraname, camera.httpurl, camera.rstpurl, camera.location))
        else:
            self.capture = cv2.VideoCapture(0)
            logging.info("Video Capture device is default webcam")
        self.capture.set(cv2.CAP_PROP_FPS, 60)
            
        self.frame_queue = queue.Queue(maxsize=2)

    def is_running(self):
        return self.__FLAGS.RUNNING

    def record(self):
        def __rec(self):
            while self.__FLAGS.RUNNING and self.capture.isOpened():
                ret, frame = self.capture.read()
                if ret and self.frame_queue.full():
                    self.frame_queue.get()
                self.frame_queue.put(np.copy(frame))
        threading.Thread(target=__rec, args=(self,)).start()
    
    def subscribe_server(self):
        if self.__FLAGS.SUBSCRIBED:
            IN = self.subscribed_server_info["IN"]
            OUT = self.subscribed_server_info["OUT"]
            CHANNEL = self.subscribed_server_info["CHANNEL"]
            raise Exception("Client Service is subscribed to server :: {}  :: {} :: {}".format(IN, OUT, CHANNEL))
            pass
        cid = self.cid
        logging.info('Client {}'.format(cid))
        self.detect_service_line.lpush(vision_config.SERVICE_REGISTER_CLIENT, cid)
        st = time.time()
        while self.detect_service_line.exists(cid) == False:
            if time.time() - st > 10.:
                logging.exception('Wait too long!!!')
                RUNNING = False
                return
            time.sleep(0.1)
        msg = self.detect_service_line.get(cid)
        if msg is None or msg == b'NONE':
            logging.info('Server is busy!')
            self.detect_service_line.delete(cid)
            # RUNNING = False
            return
        info = msg.split()
        IN = info[0]
        OUT = info[1]
        CHANNEL = info[2]
        self.subscribe_object.subscribe(CHANNEL)
        logging.info('Connected to server :: {} :: {} :: {}'.format(IN, OUT, CHANNEL))
        self.subscribed_server_info["IN"] = IN
        self.subscribed_server_info["OUT"] = OUT
        self.subscribed_server_info["CHANNEL"] = CHANNEL
        self.__FLAGS.SUBSCRIBED = True
    
    def stop_service(self):
        self.subscribe_object.unsubscribe(self.subscribed_server_info["CHANNEL"])
        self.__FLAGS.RUNNING = False

    def request_detect_service(self, frame):
        IN = self.subscribed_server_info["IN"]
        timer = time.time()
        bytebuf = np.array([frame.shape[0], frame.shape[1], frame.shape[2]], dtype=np.uint16).tobytes() + frame.tobytes()
        self.detect_service_line.ltrim(IN, 0, 0)
        self.detect_service_line.lpush(IN, bytebuf)

    def get_response_detect_service(self):
        OUT = self.subscribed_server_info["OUT"]
        while not self.detect_service_line.exists(OUT):
            time.sleep(0.001)
            continue
        bboxes = np.frombuffer(self.detect_service_line.get(OUT), dtype=np.uint16)
        self.detect_service_line.delete(OUT)
        ret = False
        if len(bboxes) > 0:
            bboxes = np.reshape(bboxes, (-1, 4))
            ret = True
        return (ret, bboxes)
    
    def request_identify_service(self, face, tracker_id, mode):
        if mode != vision_config.ENCODE_MOD and mode != vision_config.IDEN_MOD:
            logging.error("Doesn't support mode {}".format(mode))
            return
        face = np.squeeze(face)
        if face.shape != (160, 160, 3):
            logging.error("Face Image must have shape (160, 160, 3), got {}".format(face.shape))
            return
        msg = np.array([len(tracker_id)], dtype=np.uint8).tobytes() + str.encode(tracker_id) + str.encode(mode) + face.tobytes()
        self.identify_service_line.lpush(vision_config.IDENTIFY_QUEUE, msg)

    def get_response_identify_service(self, tracker_id):
        msg = self.identify_service_line.rpop(tracker_id)
        ret = None
        if msg is not None:
            mode = msg.decode("utf-8")
            content = self.identify_service_line.rpop(tracker_id)
            # print(mode, content)
            if mode == vision_config.ENCODE_MOD:
                ret = manage_data.convert_bytes_to_embedding_vector(content)
            else:
                predict_id = int(content)
                if predict_id != -1:
                    pred_p = Person(id=predict_id)
                    ret = self.database.getPersonById(pred_p.id)
            return (mode, ret)
        return (None, None)

    def get_frame_from_queue(self):
        frame = self.frame_queue.get()
        return frame

def client(thread_idx):
    global RUNNING
    logging.info('Client {}'.format(cid))
    r = redis.StrictRedis(host='localhost', port=6379)
    r_remote = redis.StrictRedis(host='localhost', port=6379)
    p = r.pubsub()
    r.lpush(vision_config.SERVICE_REGISTER_CLIENT, cid)
    st = time.time()
    while r.exists(cid) == False:
        if time.time() - st > 10.:
            logging.exception('Wait too long!!!')
            RUNNING = False
            return
        time.sleep(0.1)
    msg = r.get(cid)
    if msg is None or msg == b'NONE':
        logging.info('Server is busy!')
        r.delete(cid)
        # RUNNING = False
        return
    info = msg.split()
    IN = info[0]
    OUT = info[1]
    CHANNEL = info[2]
    p.subscribe(CHANNEL)
    logging.info('Connected to server :: {} :: {} :: {}'.format(IN, OUT, CHANNEL))
    
    # database = manage_data.read_database_from_disk(vision_config.DATABASE_DIR, vision_config.DATABASE_NAME_LOAD)
    # vision_object = Vision(database)
    multi_tracker = MultiTracker()
    bboxes = []
    timer = time.time()
    while RUNNING:
        frame = q_array[thread_idx].get()
        fps = int(1./(time.time() - timer + 0.000001))
        timer = time.time()
        bytebuf = np.array([frame.shape[0], frame.shape[1], frame.shape[2]], dtype=np.uint16).tobytes() + frame.tobytes()
        r.ltrim(IN, 0, 0)
        r.lpush(IN, bytebuf)
        while not r.exists(OUT):
            time.sleep(0.001)
            continue
        bboxes = np.frombuffer(r.get(OUT), dtype=np.uint16)
        r.delete(OUT)
        if len(bboxes) > 0:
            bboxes = np.reshape(bboxes, (-1, 4))
        # try:
        multi_tracker.update_bounding_box(bboxes, database)
        unidentified_tracker, identified_tracker = multi_tracker.cluster_trackers()
        trackers = multi_tracker.get_multitracker()
        # t1 = time.time()
        for idx, tracker in enumerate(trackers):
            if tracker.person is None:
                msg = r_remote.rpop(tracker.id)
                if msg is not None:
                    # print("AAA {} {}".format(tracker.id, msg))
                    tracker.receive += 1
                    if tracker.person is None:
                        logging.info("{} - {}".format("Unknown", tracker.receive))
                    else:
                        logging.info("{} - {} - {}".format(tracker.person.id, tracker.person.name, tracker.receive))
                    predict_id = int(msg)
                    predicts = None
                    if predict_id != -1:
                        pred_p = Person(id=predict_id)
                        predicts = database.getPersonById(pred_p.id)
                    multi_tracker.update_identification([tracker], [predicts])
        # t2 = time.time()
        if len(unidentified_tracker) > 0:
            for tracker in unidentified_tracker:
                if tracker.person is None and tracker.tried < vision_config.NUM_TRIED and time.time() - tracker.last_time_tried >= vision_config.DELAY_TRIED:
                    len_ID = len(tracker.id)
                    ID = tracker.id
                    face = tracker.get_bbox_image(frame)
                    tracker.set_image(face)
                    msg = np.array([len_ID], dtype=np.uint8).tobytes() + str.encode(ID) + face.tobytes()
                    r_remote.lpush(vision_config.IDENTIFY_QUEUE, msg)
                    
                    # logging.info("Send an request. Delay time: {}s".format(time.time()- tracker.last_time_tried))
                    tracker.last_time_tried = time.time()
                    tracker.tried += 1
    
        multi_tracker.show_info(frame)
        cv2.putText(frame, 'FPS ' + str(fps), \
                    vision_config.FPS_POS, cv2.FONT_HERSHEY_SIMPLEX, \
                    vision_config.FONT_SIZE, vision_config.POS_COLOR, \
                    vision_config.LINE_THICKNESS, cv2.LINE_AA)
        cv2.imshow(cid, frame)  # logging.info frame
        if cv2.waitKey(1) == 27:
            p.unsubscribe(CHANNEL)
            RUNNING = False
            break
    cap.release()
    cv2.destroyAllWindows()