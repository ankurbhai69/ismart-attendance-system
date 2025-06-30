from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
import cv2
import numpy as np
import base64
from io import BytesIO
from datetime import date, datetime
from geopy.distance import geodesic

app = Flask(__name__)
# Use an environment variable for the secret key in production, with a fallback for development.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_default_development_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', 'attendance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Location Configuration ---
# These will be the default values if not set in the database.
DEFAULT_INSTITUTE_LATITUDE = 28.6139  # Example: India Gate, New Delhi
DEFAULT_INSTITUTE_LONGITUDE = 77.2295
MAX_DISTANCE_METERS = 100

db = SQLAlchemy(app)

# Models
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    face_data = db.Column(db.LargeBinary, nullable=False)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())
    student = db.relationship('Student', backref=db.backref('attendance', lazy=True))

class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200), nullable=False)

def get_setting(key, default=None):
    setting = db.session.get(Setting, key)
    return setting.value if setting else default

def create_database(app):
    with app.app_context():
        db.create_all()
        if not Admin.query.first():
            hashed_password = generate_password_hash('admin', method='pbkdf2:sha256')
            new_admin = Admin(username='admin', password=hashed_password)
            db.session.add(new_admin)
            db.session.commit()

# Routes
@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/home')
def home():
    return render_template('home.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password, password):
            session['admin_id'] = admin.id
            flash('Login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    students = Student.query.all()
    attendance_records = Attendance.query.all()
    latitude = get_setting('institute_latitude', DEFAULT_INSTITUTE_LATITUDE)
    longitude = get_setting('institute_longitude', DEFAULT_INSTITUTE_LONGITUDE)
    return render_template('admin_dashboard.html', students=students, attendance=attendance_records, latitude=latitude, longitude=longitude)

@app.route('/admin/update_settings', methods=['POST'])
def update_settings():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))

    new_lat = request.form.get('latitude')
    new_lon = request.form.get('longitude')

    if new_lat and new_lon:
        try:
            lat_val = float(new_lat)
            lon_val = float(new_lon)

            for key, value in {'institute_latitude': str(lat_val), 'institute_longitude': str(lon_val)}.items():
                setting = db.session.get(Setting, key)
                if setting:
                    setting.value = value
                else:
                    setting = Setting(key=key, value=value)
                    db.session.add(setting)
            
            db.session.commit()
            flash('Location settings updated successfully!', 'success')
        except ValueError:
            flash('Invalid latitude or longitude format. Please enter valid numbers.', 'danger')
    else:
        flash('Latitude and longitude cannot be empty.', 'danger')

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add_student', methods=['GET', 'POST'])
def add_student():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        name = request.form['name']
        image_data = request.form['image_data']
        
        try:
            img_data = base64.b64decode(image_data.split(',')[1])
            nparr = np.frombuffer(img_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.3, 5)

            if len(faces) > 0:
                (x, y, w, h) = faces[0]
                face_img = gray[y:y+h, x:x+w]
                
                _, face_blob = cv2.imencode('.jpg', face_img)
                
                new_student = Student(name=name, face_data=face_blob.tobytes())
                db.session.add(new_student)
                db.session.commit()
                flash('Student added successfully!', 'success')
                return redirect(url_for('admin_dashboard'))
            else:
                flash('No face detected. Please try again.', 'danger')
        except Exception as e:
            flash(f'An error occurred: {e}', 'danger')

    return render_template('add_student.html')

@app.route('/admin/delete_student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))

    student_to_delete = Student.query.get_or_404(student_id)
    try:
        # Delete associated attendance records first
        Attendance.query.filter_by(student_id=student_to_delete.id).delete()
        
        db.session.delete(student_to_delete)
        db.session.commit()
        flash(f'Student "{student_to_delete.name}" and all their attendance records have been deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred while deleting the student: {e}', 'danger')

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/attendance')
def take_attendance_page():
    """Renders the page for taking attendance."""
    return render_template('take_attendance.html')

@app.route('/api/recognize_and_attend', methods=['POST'])
def recognize_and_attend():
    """API endpoint for real-time face recognition and attendance marking."""
    data = request.get_json()
    image_data = data.get('image_data')
    user_lat = data.get('latitude')
    user_lon = data.get('longitude')

    if not all([image_data, user_lat, user_lon]):
        return jsonify({'status': 'error', 'message': 'Missing data.'})

    # 1. Location Check
    try:
        institute_latitude = float(get_setting('institute_latitude', DEFAULT_INSTITUTE_LATITUDE))
        institute_longitude = float(get_setting('institute_longitude', DEFAULT_INSTITUTE_LONGITUDE))
        user_location = (float(user_lat), float(user_lon))
        distance = geodesic(user_location, (institute_latitude, institute_longitude)).meters

        if distance > MAX_DISTANCE_METERS:
            return jsonify({'status': 'failure', 'reason': 'out_of_range'})
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid location data.'})

    # 2. Face Recognition
    try:
        img_data = base64.b64decode(image_data.split(',')[1])
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        if len(faces) == 0:
            return jsonify({'status': 'failure', 'reason': 'no_face'})

        (x, y, w, h) = faces[0]
        detected_face_img = gray[y:y+h, x:x+w]
        
        students = Student.query.all()
        if not students:
            return jsonify({'status': 'error', 'message': 'No students registered.'})

        recognizer = cv2.face.LBPHFaceRecognizer_create()
        labels = [student.id for student in students]
        faces_data = [cv2.imdecode(np.frombuffer(student.face_data, np.uint8), cv2.IMREAD_GRAYSCALE) for student in students]
        
        recognizer.train(faces_data, np.array(labels))
        label, confidence = recognizer.predict(detected_face_img)

        if confidence < 100:
            matched_student = Student.query.get(label)
            if matched_student:
                # 3. Check if attendance already marked today
                today = date.today()
                start_of_day = datetime.combine(today, datetime.min.time())
                existing_attendance = Attendance.query.filter(
                    Attendance.student_id == matched_student.id,
                    Attendance.timestamp >= start_of_day
                ).first()

                if existing_attendance:
                    return jsonify({'status': 'success', 'reason': 'already_marked', 'name': matched_student.name})
                
                # 4. Mark Attendance
                new_attendance = Attendance(student_id=matched_student.id)
                db.session.add(new_attendance)
                db.session.commit()
                return jsonify({'status': 'success', 'reason': 'marked', 'name': matched_student.name})

        return jsonify({'status': 'failure', 'reason': 'not_recognized'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not Admin.query.first():
            hashed_password = generate_password_hash('admin', method='pbkdf2:sha256')
            new_admin = Admin(username='admin', password=hashed_password)
            db.session.add(new_admin)
        
        # Initialize settings if they don't exist
        if not get_setting('institute_latitude'):
            db.session.add(Setting(key='institute_latitude', value=str(DEFAULT_INSTITUTE_LATITUDE)))
        if not get_setting('institute_longitude'):
            db.session.add(Setting(key='institute_longitude', value=str(DEFAULT_INSTITUTE_LONGITUDE)))
        
        db.session.commit()
    app.run(debug=True) 