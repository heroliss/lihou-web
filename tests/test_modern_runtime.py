import unittest
from app import create_app, db
from app.models import User, Role, Post, Comment


class ModernRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        with self.app.app_context():
            db.create_all()
            Role.insert_roles()
            user = User(email='demo@example.com', username='demo', password='demo-pass', confirmed=True)
            db.session.add(user)
            db.session.commit()
            post = Post(body='Initial article', author=user)
            db.session.add(post)
            db.session.commit()
            self.post_id = post.id
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, path='/auth/login'):
        return self.client.post(path, data={'email': 'demo@example.com', 'password': 'demo-pass'})

    def test_article_and_comment_persist_across_request_sessions(self):
        self.login()
        self.assertEqual(self.client.post('/', data={'body': 'Persistent article'}).status_code, 302)
        self.assertEqual(self.client.post(f'/post/{self.post_id}', data={'body': 'Persistent comment'}).status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(Post.query.filter_by(body='Persistent article').first())
            self.assertIsNotNone(Comment.query.filter_by(body='Persistent comment').first())

    def test_anonymous_comment_is_rejected(self):
        response = self.client.post(f'/post/{self.post_id}', data={'body': 'Not allowed'})
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(Comment.query.count(), 0)

    def test_login_does_not_redirect_to_another_origin(self):
        self.assertEqual(self.login('/auth/login?next=https://example.org/').headers['Location'], '/')

    def test_modern_password_hash_fits_declared_column(self):
        with self.app.app_context():
            user = User.query.filter_by(username='demo').first()
            self.assertLessEqual(len(user.password_hash), User.__table__.c.password_hash.type.length)

    def test_sample_generators_work_with_modern_faker(self):
        with self.app.app_context():
            User.generate_fake(2)
            Post.generate_fake(2)
            Comment.generate_fake(2)
            self.assertEqual(User.query.count(), 3)
            self.assertEqual(Post.query.count(), 3)
            self.assertEqual(Comment.query.count(), 2)
