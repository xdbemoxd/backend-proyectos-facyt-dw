from pydantic import BaseModel, EmailStr

class UserValidation( BaseModel ):
    email: str
    password: str

class UserCreated( BaseModel ):
    name:str
    lastname:str
    email:str
    password:str | None

class EmailRequestUser( BaseModel ):
    email: EmailStr

class GoogleAuthRequest( BaseModel ):
    id_token: str
