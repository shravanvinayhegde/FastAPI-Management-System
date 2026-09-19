import bcrypt

class CryptContext:
    def hash(self, password: str) -> str:
        password_bytes = password.encode('utf-8')
        if len(password_bytes) > 72:
            raise ValueError("Password cannot exceed 72 bytes")
        return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        password_bytes = plain_password.encode('utf-8')
        if len(password_bytes) > 72:
            return False
        return bcrypt.checkpw(password_bytes, hashed_password.encode('utf-8'))

pwd_context = CryptContext()

# Add these standalone functions
def hash(password: str) -> str:
    return pwd_context.hash(password)

def verify(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)