import os
import json
import random
import urllib2
import uuid
import cgi
from datetime import datetime
from urlparse import urlparse
import mimetypes
import logging
import logging.handlers

from twilio.rest import TwilioRestClient
from flask import Flask, request
import dateutil.parser
import cv2
import boto3

ACCOUNT_SID = os.environ['TWILIO_ACCOUNT_SID']
AUTH_TOKEN = os.environ['TWILIO_AUTH_TOKEN']
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
LOG_PATH = os.environ['LOG_PATH']

logger = logging.getLogger('sms-playground')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)-15s %(levelname)-8s %(message)s')
fileHandler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=10*1024*1024, backupCount=5)
fileHandler.setLevel(logging.DEBUG)
fileHandler.setFormatter(formatter)
logger.addHandler(fileHandler)
streamHandler = logging.StreamHandler()
streamHandler.setLevel(logging.DEBUG)
streamHandler.setFormatter(formatter)
logger.addHandler(streamHandler)

logger.info("Started server.")

twilio = TwilioRestClient(ACCOUNT_SID, AUTH_TOKEN)
app = Flask(__name__)

# Keeps track of which text messages we've already handled
# and shouldn't get processed again
handled_messages = set()

# Maps a conversation code to a user's cell phone number
conversation_to_phone_number = {}
pictures = {}


#-----------------------------------------------------------------------------
# Configure Haar Cascade Classifiers
#-----------------------------------------------------------------------------

# location of OpenCV Haar Cascade Classifiers:
baseCascadePath = "./cascades/"

# xml files describing our haar cascade classifiers
faceCascadeFilePath = baseCascadePath + "haarcascade_frontalface_default.xml"
noseCascadeFilePath = baseCascadePath + "haarcascade_mcs_nose.xml"
eyeCascadeFilePath = baseCascadePath + "haarcascade_eye.xml"

# build our cv2 Cascade Classifiers
faceCascade = cv2.CascadeClassifier(faceCascadeFilePath)
noseCascade = cv2.CascadeClassifier(noseCascadeFilePath)
eyeCascade = cv2.CascadeClassifier(eyeCascadeFilePath)


#-----------------------------------------------------------------------------
# API Endpoints
#-----------------------------------------------------------------------------

@app.route("/", methods=['GET'])
def index():
    return "<html><head><title>KID Museum: SMS Playground</title><body>It works.</body></html>"

@app.route("/conversation/start", methods=['POST'])
def start_a_conversation():
    response = None

    request_data = request.get_json()
    keyword = request_data['keyword']
    oldest_message_time = dateutil.parser.parse(request_data['messages_must_be_older_than'])

    # Check if any users have sent a text to the server with the keyword used to start the conversation,
    # making sure the message wasn't already handled earlier and isn't from a long time ago
    for message in twilio.messages.list(date_sent=datetime.utcnow().date()):
        if message.sid not in handled_messages and message.date_created >= oldest_message_time:

            # If this message doesn't match our keyword, try the next message
            if message.body.strip().lower() != keyword.strip().lower():
                continue

            # Remember this message so we won't process it a second time later
            handled_messages.add(message.sid)

            # Create a new special code for the conversation
            conversation_code = make_unique_id()

            # Link the new special code to this phone number so any future messages
            # from this phone number will be associated with this conversation.
            conversation_to_phone_number[conversation_code] = message.from_

            # Tell the program to use the special conversation code when it wants
            # to send this user any text messages and get replies
            response = {'conversation_code': conversation_code}

            logger.info("Created conversation for {} via keyword {} ({})".format(
                conversation_to_phone_number[conversation_code], keyword, conversation_code))

            break

    # if we didn't find any messages that are starting a conversation,
    # tell the program to wait a little bit and check again
    if response is None:
        response = {'wait_for_seconds': 1}

    return json.dumps(response), 200, {'Content-Type': 'application/json'}


@app.route("/conversation/<conversation_code>/message/send", methods=['POST'])
def send_message(conversation_code):
    request_data = request.get_json()
    message = request_data['message']
    picture_url = request_data.get('picture_url', None)

    # Send a new message to the user in the conversation
    if conversation_code in conversation_to_phone_number:
        _send_message(conversation_code, message, picture_url)
        return "", 200
    else:
        return "No conversation found with specified code", 404


@app.route("/conversation/<conversation_code>/message/response/<expected_response_type>", methods=['POST'])
def get_response_message(conversation_code, expected_response_type):
    response = None

    request_data = request.get_json()
    oldest_message_time = dateutil.parser.parse(request_data['messages_must_be_older_than'])

    # Get a message sent to the sms playground that's for this conversation
    # and hasn't already been handled earlier
    if conversation_code in conversation_to_phone_number:
        users_phone_number = conversation_to_phone_number[conversation_code]
        for message in twilio.messages.list(from_=users_phone_number):
            if message.sid not in handled_messages and message.date_created >= oldest_message_time:

                logger.info("Received {} message from {}: {}{} ({})".format(
                    expected_response_type, users_phone_number, "'{}'".format(message.body),
                    "|{}".format(message.media_list.list()[0].uri) if expected_response_type == "picture" and int(message.num_media) > 0 else "",
                    conversation_code
                ))

                # Remember this message so we won't process it a second time later
                handled_messages.add(message.sid)

                # Make sure the message sent from the user matches what the program
                # was expecting (e.g. a number or a picture). If it's not, ask the
                # user to send another message that's the correct type

                if expected_response_type == "string":
                    response = {
                        'message': message.body
                    }

                elif expected_response_type == "int":
                    try:
                        response = {
                            'message': int(message.body)
                        }
                    except ValueError:
                        _send_message(conversation_code, "Whole numbers only, please. Try again.")

                elif expected_response_type == "float":
                    try:
                        response = {
                            'message': float(message.body)
                        }
                    except ValueError:
                        _send_message(conversation_code, "Numbers only, please. Try again.")

                elif expected_response_type == "picture":
                    if int(message.num_media) > 0:
                        picture_code = make_unique_id()
                        pictures[picture_code] = {
                            'url': message.media_list.list()[0].uri,
                            'moustache': None,
                            'glasses': None,
                            'lefteye': None,
                            'righteye': None,
                            'leftcheek': None,
                            'rightcheeck': None,
                        }
                        response = {
                            'picture_code': picture_code,
                        }

                        logger.info("Created picture for {} ({}) ({})".format(
                            conversation_to_phone_number[conversation_code], conversation_code, picture_code))
                    else:
                        _send_message(conversation_code, "Please reply with a picture.")

                break

    # If the user didn't reply to our last message yet,
    # tell the program to wait a little bit and check again
    if response is None:
        response = {'wait_for_seconds': 1}

    return json.dumps(response), 200, {'Content-Type': 'application/json'}


@app.route("/conversation/<conversation_code>/picture/<picture_code>/<area>", methods=['POST'])
def add_to_picture(conversation_code, picture_code, area):
    request_data = request.get_json()

    if area == "moustache":
        moustache_name = request_data['moustache_name']
        if not os.path.exists(get_moustache_path(moustache_name)):
            return "There isn't a moustache with the name {}".format(moustache_name), 404
        pictures[picture_code][area] = moustache_name
        logger.info("Added {} to {} ({}) ({})".format(
            request_data['moustache_name'], area, conversation_code, picture_code))

    elif area == "glasses":
        glasses_name = request_data['glasses_name']
        if not os.path.exists(get_glasses_path(glasses_name)):
            return "There isn't a moustache with the name {}".format(glasses_name), 404
        pictures[picture_code][area] = glasses_name
        logger.info("Added {} to {} ({}) ({})".format(
            request_data['glasses_name'], area, conversation_code, picture_code))

    else:
        return "Area {} is not supported".format(area), 404

    return "", 200


@app.route("/conversation/<conversation_code>/picture/<picture_code>/", methods=['GET'])
def get_transformed_picture(conversation_code, picture_code):
    # Download the picture
    url = pictures[picture_code]['url']
    image = get_image(url)
    transformed_image_path = None

    try:
        # Apply all the transforms queued up by earlier API calls (i.e. add_to_picture calls)
        transform_image(image, pictures[picture_code])

        # Save the transformed picture and upload it to S3 (file storage in the cloud)
        file_extension = get_file_extension_from_url(url)
        file_extension = ".{}".format(file_extension) if file_extension else ""
        filename = '{}{}'.format(make_unique_id(), file_extension)
        transformed_image_path = 'images/{}'.format(filename)
        cv2.imwrite(transformed_image_path, image)
        s3file = boto3.resource('s3', verify=False).Object('sms-playground', filename)
        s3file.put(Body=open(transformed_image_path, 'rb'), ACL='public-read',
                   ContentType=mimetypes.guess_type(filename)[0])

        logger.info("Transformed picture and saved to {} ({}) ({})".format(
            filename, conversation_code, picture_code))

    finally:
        if transformed_image_path and os.path.exists(transformed_image_path):
            os.remove(transformed_image_path)

    return json.dumps({'url': 'https://s3.amazonaws.com/sms-playground/{}'.format(filename)})


@app.errorhandler(500)
def internal_error(exception):
    logger.error(exception)
    return "", 500


# ----------------------------------------------------------------------------
# Image transform functions
# ----------------------------------------------------------------------------

def add_moustache(image, face_xywh, nose_xywh, moustache_name):
    x, y, w, h = face_xywh
    nx, ny, nw, nh = nose_xywh

    # Load the moustache image we're adding to the image
    imgMustache = cv2.imread(get_moustache_path(moustache_name), -1)

    # Create the mask for the moustache
    orig_mask = imgMustache[:,:,3]

    # Create the inverted mask for the moustache
    orig_mask_inv = cv2.bitwise_not(orig_mask)

    # Convert moustache image to BGR
    # and save the original image size (used later when re-sizing the image)
    imgMustache = imgMustache[:,:,0:3]
    origMustacheHeight, origMustacheWidth = imgMustache.shape[:2]

    # The moustache should be three times the width of the nose
    moustacheWidth =  int(2 * nw)
    moustacheHeight = int(origMustacheHeight * (float(moustacheWidth) / origMustacheWidth))

    # Center the moustache on the bottom of the nose
    x1 = (nx + (nw / 2)) - (moustacheWidth / 2)
    x2 = x1 + moustacheWidth
    y1 = ny + (nh / 2)
    y2 = y1 + moustacheHeight

    # Check for clipping
    if x1 < 0:
        x1 = 0
    if y1 < 0:
        y1 = 0
    if x2 > w:
        x2 = w
    if y2 > h:
        y2 = h

    # Re-calculate the width and height of the moustache image
    moustacheWidth = x2 - x1
    moustacheHeight = y2 - y1

    # Re-size the original image and the masks to the moustache sizes
    # calcualted above
    moustache = cv2.resize(imgMustache, (moustacheWidth,moustacheHeight), interpolation = cv2.INTER_AREA)
    mask = cv2.resize(orig_mask, (moustacheWidth,moustacheHeight), interpolation = cv2.INTER_AREA)
    mask_inv = cv2.resize(orig_mask_inv, (moustacheWidth,moustacheHeight), interpolation = cv2.INTER_AREA)

    # take ROI for moustache from background equal to size of moustache image
    roi = image[y1:y2, x1:x2]

    # roi_bg contains the original image only where the moustache is not
    # in the region that is the size of the moustache.
    roi_bg = cv2.bitwise_and(roi,roi,mask = mask_inv)

    # roi_fg contains the image of the moustache only where the moustache is
    roi_fg = cv2.bitwise_and(moustache,moustache,mask = mask)

    # join the roi_bg and roi_fg
    dst = cv2.add(roi_bg,roi_fg)

    # place the joined image, saved to dst back over the original image
    image[y1:y2, x1:x2] = dst


def add_glasses(image, face_xywh, eyes_xywh, glasses_name):
    x, y, w, h = face_xywh
    ex, ey, ew, eh = eyes_xywh

    # Load glasses we're adding to the image
    imgGlasses = cv2.imread(get_glasses_path(glasses_name), -1)

    # Create the mask for the glasses
    orig_mask_sg = imgGlasses[:,:,3]

    # Create the inverted mask for the glasses
    orig_mask_inv_sg = cv2.bitwise_not(orig_mask_sg)

    # Convert glasses image to BGR
    # and save the original image size (used later when re-sizing the image)
    imgGlasses = imgGlasses[:,:,0:3]
    origGlassesHeight, origGlassesWidth = imgGlasses.shape[:2]

    # The glasses should overlap the eyes a little bit
    glassesWidth =  int(ew * 1.3)
    glassesHeight = int(origGlassesHeight * (float(glassesWidth) / origGlassesWidth))

    # Center the glasses over the eyes
    x1 = (ex + (ew / 2)) - (glassesWidth / 2)
    x2 = x1 + glassesWidth
    y1 = ey
    y2 = ey + glassesHeight

    # Check for clipping
    if x1 < 0:
        x1 = 0
    if y1 < 0:
        y1 = 0
    if x2 > w:
        x2 = w
    if y2 > h:
        y2 = h

    # Re-calculate the width and height of the glasses image
    glassesWidth = x2 - x1
    glassesHeight = y2 - y1

    # Re-size the original image and the masks to the glasses sizes
    # calcualted above
    glasses = cv2.resize(imgGlasses, (glassesWidth,glassesHeight), interpolation = cv2.INTER_AREA)
    mask = cv2.resize(orig_mask_sg, (glassesWidth,glassesHeight), interpolation = cv2.INTER_AREA)
    mask_inv = cv2.resize(orig_mask_inv_sg, (glassesWidth,glassesHeight), interpolation = cv2.INTER_AREA)

    # take ROI for glasses from background equal to size of glasses image
    roi = image[y1:y2, x1:x2]

    # roi_bg contains the original image only where the glasses is not
    # in the region that is the size of the glasses.
    roi_bg = cv2.bitwise_and(roi,roi,mask = mask_inv)

    # roi_fg contains the image of the glasses only where the glasses is
    roi_fg = cv2.bitwise_and(glasses,glasses,mask = mask)

    # join the roi_bg and roi_fg
    dst = cv2.add(roi_bg,roi_fg)

    # place the joined image, saved to dst back over the original image
    image[y1:y2, x1:x2] = dst


# ----------------------------------------------------------------------------
# Support functions
# ----------------------------------------------------------------------------
def get_moustache_path(moustache_name):
    return 'images/moustaches/{}.png'.format(moustache_name)


def get_glasses_path(glasses_name):
    return 'images/glasses/{}.png'.format(glasses_name)


def _send_message(conversation_code, message, picture_url=None):
    args = {
        'body': message,
        'to': conversation_to_phone_number[conversation_code],
        'from_': "+12407536527",
    }
    if picture_url:
        args['media_url'] = picture_url
    twilio.messages.create(**args)
    logger.info("Sent message to {}: {}{} ({})".format(
        conversation_to_phone_number[conversation_code], message,
        "|{}".format(picture_url) if picture_url else "", conversation_code))


def make_unique_id():
    return "%032x" % random.getrandbits(128)


def get_file_extension_from_url(url):
    request = urllib2.Request(url, headers={ 'User-Agent': 'Mozilla/5.0' })
    response = urllib2.urlopen(request)
    if 'content-disposition' in response.headers and 'filename=' in response.headers['content-disposition']:
        file_name = cgi.parse_header(response.headers['content-disposition'])[1]['filename']
        file_extension = os.path.splitext(file_name)[1]
        if file_extension.startswith("."):
            file_extension = file_extension[1:]
    elif 'content-type' in response.headers:
        file_extension = response.headers['content-type'].split("/")[1]
    else:
        file_extension = os.path.splitext(urlparse(url).path)[1]
    return file_extension


def get_image(url):
    image_path = None
    try:
        # Download the image to the server's hard drive and load it so we can manipulate it
        file_extension = get_file_extension_from_url(url)
        file_extension = ".{}".format(file_extension) if file_extension else ""
        image_path = "images/{}{}".format(str(uuid.uuid4()).replace("-", ""), file_extension)
        request = urllib2.Request(url, headers={ 'User-Agent': 'Mozilla/5.0' })
        response = urllib2.urlopen(request)
        with open(image_path, "w") as saved_image:
            saved_image.write(response.read())
        image = cv2.imread(image_path)

        # Make sure the image is a small enough size for the detection algoritms
        # to work with an acceptable accuracy
        original_height, original_width = image.shape[:2]
        if max(original_height, original_width) > 640:
            if original_height > original_width:
                image = cv2.resize(image, (int(original_width * (640.0 / original_height)), 640))
            else:
                image = cv2.resize(image, (640, int(original_height * (640.0 / original_width))))
    finally:
        pass
        # if image_path and os.path.exists(image_path):
        #     os.remove(image_path)

    return image


def transform_image(image, transform_info):
    # Get a greyscale version of the image to help with calculations
    grayscale_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Detect the position of a face in the image
    faces_found = faceCascade.detectMultiScale(
        grayscale_image,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
        flags=cv2.CASCADE_SCALE_IMAGE
    )

    # Detect the eyes and nose if we found a face in the image
    if len(faces_found) > 0:
        face_xywh = x, y, w, h = faces_found[0]
        face_gray = grayscale_image[y:y+h, x:x+w]
        face_color = image[y:y+h, x:x+w]

        nose_xywh = None
        noses_found = noseCascade.detectMultiScale(face_gray)
        if len(noses_found) > 0:
            nose_xywh = noses_found[0]

        eyes_xywh = None
        eyes = eyeCascade.detectMultiScale(face_gray)
        if len(eyes) == 2:
            # Bounding boxes for each eye
            left_eye_xywh = eyes[0] if eyes[0][0] < eyes[1][0] else eyes[1]
            right_eye_xywh = eyes[0] if eyes[0][0] >= eyes[1][0] else eyes[1]

            # Bounding box for both eyes
            eyes_xywh = (
                left_eye_xywh[0],
                min(eyes[0][1], eyes[1][1]),
                right_eye_xywh[0] + right_eye_xywh[2] - left_eye_xywh[0],
                max(left_eye_xywh[1] + left_eye_xywh[3], right_eye_xywh[1] + right_eye_xywh[3]) - min(left_eye_xywh[1], right_eye_xywh[1])
            )

        # Apply transforms for the face
        if transform_info['moustache'] and nose_xywh is not None:
            add_moustache(face_color, face_xywh, nose_xywh, transform_info['moustache'])

        if transform_info['glasses'] and eyes_xywh is not None:
            add_glasses(face_color, face_xywh, eyes_xywh, transform_info['glasses'])


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)
